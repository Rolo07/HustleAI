"""Owner-only bank imports and reviewed payment mutations using the tenant journal."""
import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from hustleai.workflows.bank_reconciliation import parse_statement, reconcile, markdown_report, normalized
from hustleai.workflows.service import Service
from hustleai.domain.validation import identifier


class BankReconciliation:
    def __init__(self, engine):
        self.engine = engine
        with engine.journal.locked():
            state = engine.journal.read(); changed = False
            for record in state.get('bank_payments', {}).values():
                if record['state'] in ('preparing', 'attempting'):
                    record['state'] = 'uncertain'; changed = True
            for notice in state.get('bank_notifications', {}).values():
                if notice.get('email') == 'attempting':
                    notice['email'] = 'failed_or_uncertain; inspect provider before retry'; changed = True
            if changed:
                engine.journal.write(state)

    @staticmethod
    def receipt_key(report, row):
        # Identical evidence without a bank ID cannot safely prove a second receipt.
        identity = [normalized(report['account_label']), row['transaction_id']] if row['transaction_id'] else [
            row['invoice_id'], row['date'], str(Decimal(row['amount']).quantize(Decimal('.01'))),
            normalized(row['reference']) or normalized(row['name'] + ' ' + row['description'])]
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def refresh(self, rid):
        old = self.report(rid)
        invoices, payments = self.ledger()
        rows = [{k: row[k] for k in ('row', 'date', 'amount', 'description', 'reference', 'name', 'transaction_id')} for row in old['transactions']]
        fresh = reconcile(rows, invoices, payments, old['period_start'], old['period_end'], old['parse_issues'])
        fresh.update(id=old['id'], source_hash=old['source_hash'], account_label=old['account_label'], demo=self.engine.demo,
                     revision=old.get('revision', 0) + 1)
        with self.engine.journal.locked():
            state = self.engine.journal.read()
            current = state['bank_reports'][rid]
            if current.get('revision', 0) != old.get('revision', 0):
                raise ValueError('Report changed during refresh; reload before trying again.')
            for row in fresh['transactions']:
                if row['status'] != 'suggested_match':
                    continue
                saved = state.get('bank_payments', {}).get(self.receipt_key(fresh, row))
                if saved and saved['state'] == 'recorded':
                    row.update(status='recorded_match', reason='This receipt was already owner-confirmed and recorded by HustleAI.')
                    for item in fresh['outstanding']:
                        if item['invoice_id'] == row['invoice_id']:
                            remaining = max(Decimal('0'), Decimal(item['suggested_bank_credits']) - Decimal(row['amount']))
                            item.update(suggested_bank_credits=str(remaining), unexplained_balance=str(max(Decimal('0'), Decimal(item['ledger_balance']) - remaining)))
            state['bank_reports'][rid] = fresh
            self.engine.journal.write(state)
        return fresh

    def ledger(self):
        if self.engine.demo:
            invoices = [{'invoice_id': '9001', 'invoice_number': 'RG-1001', 'customer_id': '8001', 'customer_name': 'Maya Botha',
                      'date': '2026-10-01', 'due_date': '2026-10-08', 'total': '440.00', 'balance': '440.00', 'currency_code': 'ZAR', 'status': 'overdue'},
                     {'invoice_id': '9002', 'invoice_number': 'RG-1002', 'customer_id': '8002', 'customer_name': 'Daniel Jacobs',
                      'date': '2026-10-02', 'due_date': '2026-10-09', 'total': '376.00', 'balance': '376.00', 'currency_code': 'ZAR', 'status': 'overdue'}]
            with self.engine.journal.locked():
                recorded = self.engine.journal.read().get('bank_payments', {}).values()
                for record in recorded:
                    if record['state'] != 'recorded':
                        continue
                    value = Decimal(str(record.get('preview', {}).get('payment', {}).get('amount', '0')))
                    for inv in invoices:
                        if inv['invoice_id'] == record['invoice_id']:
                            inv['balance'] = str(max(Decimal('0'), Decimal(inv['balance']) - value))
                            inv['status'] = 'paid' if Decimal(inv['balance']) == 0 else 'partially_paid'
            return invoices, []
        with Service(root=self.engine.business.root) as service:
            if service.tenant.currency != 'ZAR':
                raise ValueError('Statement reconciliation currently supports ZAR tenants only.')
            # Never use stale cached orders when live provider reads fail.
            from hustleai.workflows.invoice_review import eligible_order
            invoices = []
            for summary in service.api.pages('invoices', 'invoices'):
                if not eligible_order(summary, service.workflow_config.get('test_invoice_ids', ())):
                    continue
                required = ('currency_code', 'customer_id', 'total', 'balance', 'date', 'due_date')
                full = summary if all(k in summary for k in required) else service.api.get('invoices/' + identifier(summary['invoice_id']))['invoice']
                if eligible_order(full, service.workflow_config.get('test_invoice_ids', ())):
                    invoices.append(full)
            return invoices, list(service.api.pages('customerpayments', 'customerpayments'))

    def import_csv(self, data):
        if data.get('currency') != 'ZAR' or data.get('reviewed') is not True:
            raise ValueError('Confirm the statement is in ZAR and you reviewed extraction before reconciling.')
        content = data.get('content')
        if not isinstance(content, str):
            raise ValueError('CSV text required.')
        account = str(data.get('account_label', '')).strip()
        if not account or len(account) > 80:
            raise ValueError('Enter a bank account label (not the account number), up to 80 characters.')
        options = data.get('options') or {}
        rows, issues = parse_statement(content, options)
        start = data.get('period_start'); end = data.get('period_end')
        # Explicit coverage, never infer a statement period from credit-only rows.
        try:
            date.fromisoformat(start); date.fromisoformat(end)
        except (TypeError, ValueError):
            raise ValueError('Enter statement start/end dates in YYYY-MM-DD format.') from None
        source_hash = hashlib.sha256(json.dumps([account, content, options, start, end], sort_keys=True).encode()).hexdigest()
        with self.engine.journal.locked():
            state = self.engine.journal.read()
            for report in state.get('bank_reports', {}).values():
                if report['source_hash'] == source_hash:
                    return report
        invoices, payments = self.ledger()
        report = reconcile(rows, invoices, payments, start, end, issues)
        report.update(id=source_hash[:32], source_hash=source_hash, account_label=account, demo=self.engine.demo)
        with self.engine.journal.locked():
            state = self.engine.journal.read()
            for existing in state.get('bank_reports', {}).values():
                if existing['source_hash'] == source_hash:
                    return existing
            state.setdefault('bank_reports', {})[report['id']] = report
            state['activity'].insert(0, {'at': datetime.now(timezone.utc).isoformat(), 'action': 'Bank statement reconciled', 'conversation': '',
                                         'detail': str(len(report['transactions'])) + ' transactions; owner report ready'})
            self.engine.journal.write(state)
        return report

    def reports(self):
        with self.engine.journal.locked():
            state = self.engine.journal.read()
            return {'reports': sorted(state.get('bank_reports', {}).values(), key=lambda x: x['created_at'], reverse=True),
                    'payments': state.get('bank_payments', {})}

    def report(self, rid):
        with self.engine.journal.locked():
            report = self.engine.journal.read().get('bank_reports', {}).get(rid)
            if not report:
                raise ValueError('Report not found.')
            return report

    def prepare(self, rid, row_number):
        with self.engine.journal.locked():
            state = self.engine.journal.read()
            report = state.get('bank_reports', {}).get(rid)
            if not report:
                raise ValueError('Report not found.')
            row = next((x for x in report['transactions'] if x['row'] == row_number), None)
            if not row or row['status'] != 'suggested_match':
                raise ValueError('Only a uniquely suggested match can prepare a payment. Resolve other rows separately.')
            if report['parse_issues'] or report['excluded_invoices']:
                raise ValueError('Resolve incomplete statement or invoice coverage before recording payments.')
            key = self.receipt_key(report, row)
            records = state.setdefault('bank_payments', {})
            if key in records:
                return records[key]
            record = {'key': key, 'report_id': rid, 'row': row_number, 'state': 'preparing', 'invoice_id': row['invoice_id']}
            records[key] = record
            self.engine.journal.write(state)
            try:
                reference = 'BANK-' + key[:32]
                if self.engine.demo:
                    invoice = next(i for i in self.ledger()[0] if i['invoice_id'] == row['invoice_id'])
                    if Decimal(row['amount']) > Decimal(invoice['balance']):
                        raise ValueError('Payment exceeds the current demo balance.')
                    proposal = {'operation_id': key[:32], 'preview': {'record_only_no_charge': True, 'currency': 'ZAR', 'payment': {
                        'amount': row['amount'], 'date': row['date'], 'reference_number': reference,
                        'invoices': [{'invoice_id': row['invoice_id'], 'amount_applied': row['amount']}]}},
                        'confirmation_required': 'CONFIRM ' + key[:32]}
                else:
                    with Service(root=self.engine.business.root) as service:
                        candidate = row['candidates'][0]
                        invoice = service.api.get('invoices/' + identifier(row['invoice_id']))['invoice']
                        if str(invoice['customer_id']) != candidate['customer_id']:
                            raise ValueError('Invoice customer changed; reconcile again.')
                        if invoice.get('currency_code') != 'ZAR' or invoice.get('status') not in ('sent', 'overdue', 'unpaid', 'partially_paid'):
                            raise ValueError('Invoice is not an issued payable ZAR invoice.')
                        value = Decimal(row['amount'])
                        if value <= 0 or value > Decimal(str(invoice['balance'])):
                            raise ValueError('Payment exceeds the current balance; review partial/split allocations.')
                        for existing in service.api.pages('customerpayments?customer_id=' + identifier(invoice['customer_id']), 'customerpayments'):
                            if existing.get('reference_number') == reference:
                                raise ValueError('This bank transaction was already recorded; reconcile it manually.')
                        payload = {'customer_id': str(invoice['customer_id']), 'payment_mode': 'banktransfer', 'amount': float(value),
                                   'date': row['date'], 'reference_number': reference,
                                   'description': 'Bank receipt reference: ' + row['reference'][:200],
                                   'invoices': [{'invoice_id': row['invoice_id'], 'amount_applied': float(value)}]}
                        record['customer_id'] = payload['customer_id']
                        proposal = service.proposal('payment', payload, {'record_only_no_charge': True, 'currency': 'ZAR',
                                                   'customer_name': candidate['customer_name'], 'invoice_number': candidate['invoice_number'], 'payment': payload})
                record.update(state='needs_confirmation', **proposal)
            except Exception:
                record['state'] = 'blocked'
                self.engine.journal.write(state)
                raise
            state['activity'].insert(0, {'at': datetime.now(timezone.utc).isoformat(), 'action': 'Bank payment proposed', 'conversation': '', 'detail': 'Owner receipt confirmation required'})
            self.engine.journal.write(state)
            return record

    def confirm(self, key, confirmation):
        with self.engine.journal.locked():
            state = self.engine.journal.read()
            record = state.get('bank_payments', {}).get(key)
            if not record:
                raise ValueError('Payment preview not found.')
            if record['state'] == 'recorded':
                return record
            if record['state'] != 'needs_confirmation':
                raise ValueError('Payment outcome needs manual reconciliation; do not retry the provider write.')
            if confirmation != 'CONFIRM ' + record['operation_id']:
                raise ValueError('Enter the exact owner confirmation shown in the payment preview.')
            record['state'] = 'attempting'
            self.engine.journal.write(state)
            try:
                if not self.engine.demo:
                    with Service(root=self.engine.business.root) as service:
                        with service.invoice_lock(record['invoice_id']):
                            current = service.api.get('invoices/' + identifier(record['invoice_id']))['invoice']
                            if current.get('currency_code') != 'ZAR' or current.get('status') not in ('sent', 'overdue', 'unpaid', 'partially_paid') or str(current.get('customer_id')) != record.get('customer_id'):
                                raise ValueError('Invoice identity, currency or status changed; nothing submitted.')
                            service.confirm(record['operation_id'], confirmation)
                record['state'] = 'recorded'
                state['activity'].insert(0, {'at': datetime.now(timezone.utc).isoformat(), 'action': 'Bank payment recorded', 'conversation': '',
                                             'detail': 'Simulated only' if self.engine.demo else 'Owner-confirmed Zoho payment allocation'})
            except Exception:
                record['state'] = 'uncertain'
                self.engine.journal.write(state)
                raise
            self.engine.journal.write(state)
            return record

    def assign(self, rid, row_number, invoice_id):
        with self.engine.journal.locked():
            state = self.engine.journal.read()
            report = state.get('bank_reports', {}).get(rid)
            if not report:
                raise ValueError('Report not found.')
            if any(p['report_id'] == rid and p['row'] == row_number for p in state.get('bank_payments', {}).values()):
                raise ValueError('Payment already prepared; reconcile it before changing this allocation.')
            row = next((x for x in report['transactions'] if x['row'] == row_number), None)
            inv = next((x for x in report['invoice_options'] if x['invoice_id'] == invoice_id), None)
            if not row or not inv or row['status'] not in ('unmatched', 'ambiguous', 'review'):
                raise ValueError('Choose a reviewable incoming credit and a current invoice.')
            if not inv['date'] <= row['date'] or not 0 < Decimal(row['amount']) <= Decimal(inv['balance']):
                raise ValueError('Invoice date or balance does not support this allocation.')
            row.update(invoice_id=invoice_id, candidates=[inv], status='suggested_match', reason='Explicit owner invoice selection; receipt confirmation still required.')
            for outstanding in report['outstanding']:
                if outstanding['invoice_id'] == invoice_id:
                    credits = Decimal(outstanding['suggested_bank_credits']) + Decimal(row['amount'])
                    outstanding.update(suggested_bank_credits=str(credits), unexplained_balance=str(max(Decimal('0'), Decimal(outstanding['ledger_balance']) - credits)), status='Review bank credits / ledger')
            report['revision'] = report.get('revision', 0) + 1
            state['activity'].insert(0, {'at': datetime.now(timezone.utc).isoformat(), 'action': 'Bank credit assigned for review', 'conversation': '', 'detail': 'Row ' + str(row_number) + '; payment not yet recorded'})
            self.engine.journal.write(state)
            return report

    def notice_status(self, state, key, notice):
        from collections import Counter
        result = dict(notice)
        rows = [r for k, r in state['outbox'].items() if k.startswith('bank-report:' + key + ':')]
        if rows:
            states = Counter(row['state'] for row in rows)
            result['whatsapp'] = ('Simulated: ' if self.engine.demo else '') + ', '.join(str(count) + ' ' + status for status, count in states.items())
            reasons = {r.get('hold_reason') for r in rows if r.get('hold_reason')}
            if reasons:
                result['whatsapp'] += '; ' + ' '.join(sorted(reasons))
            result['whatsapp'] += '; API acceptance is not confirmed delivery'
        return result

    def notify(self, rid):
        from hustleai.dashboard.engine import conversation
        from hustleai.integrations.whatsapp.test_assistant import RECIPIENT
        import os
        report = self.report(rid)
        revision = report.get('revision', 0)
        key = rid + ':' + str(revision)
        alerts = [x for x in report['outstanding'] if Decimal(x['unexplained_balance']) > 0]
        text = 'RG Midrand: potential missing payments\n' + report['period_start'] + ' to ' + report['period_end'] + '\n'
        text += '\n'.join(f"{x['customer_name']} / {x['invoice_number']}: unexplained ZAR {x['unexplained_balance']} (ledger {x['ledger_balance']})" for x in alerts)
        text += '\nThese are review flags, not confirmed nonpayment. Other accounts/cash and parsing gaps may affect results. Full report is in your owner dashboard.'
        with self.engine.journal.locked():
            state = self.engine.journal.read()
            notices = state.setdefault('bank_notifications', {})
            existing = notices.get(key)
            if existing and not (existing['email'] == 'not_configured' and os.getenv('HUSTLEAI_REPORT_EMAIL') and os.getenv('HUSTLEAI_SMTP_HOST')):
                return self.notice_status(state, key, existing)
            notice = existing or {'dashboard': 'ready', 'whatsapp': 'not_needed', 'email': 'not_needed'}
            notices[key] = notice
            if alerts and not existing:
                owner = conversation(state, RECIPIENT, 'Roland · owner reports')
                chunks = [text[i:i + 3000] for i in range(0, len(text), 3000)]
                for i, chunk in enumerate(chunks):
                    outbound_key = 'bank-report:' + key + ':' + str(i)
                    self.engine.queue(state, owner, chunk, 'owner', key=outbound_key)
                    state['outbox'][outbound_key]['version'] = None
                notice['whatsapp'] = 'queued; Meta acceptance and delivery are tracked in activity'
                notice['email'] = 'not_configured'
            self.engine.journal.write(state)
            if alerts and os.getenv('HUSTLEAI_REPORT_EMAIL') and os.getenv('HUSTLEAI_SMTP_HOST'):
                notice['email'] = 'attempting'
                self.engine.journal.write(state)
                try:
                    if not self.engine.demo:
                        from hustleai.integrations.report_email import send_report
                        send_report(report)
                    notice['email'] = 'simulated' if self.engine.demo else 'SMTP accepted; delivery not confirmed'
                except Exception:
                    notice['email'] = 'failed_or_uncertain; check mailbox/provider before retrying'
                self.engine.journal.write(state)
            return self.notice_status(state, key, notice)
