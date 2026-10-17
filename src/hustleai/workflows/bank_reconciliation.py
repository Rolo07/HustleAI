"""Read-only statement matching. Never records payments or contacts customers."""
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import re
import unicodedata
from collections import Counter

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000


def normalized(value):
    return re.sub(r'[^a-z0-9]+', ' ', unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode().lower()).strip()


def amount(value, decimal_separator='.'):
    text = str(value).strip().replace('\u00a0', '').replace(' ', '')
    text = re.sub(r'^(?:ZAR|R)', '', text, flags=re.I)
    if text.startswith('(') and text.endswith(')'):
        text = '-' + text[1:-1]
    if decimal_separator == ',':
        text = text.replace('.', '').replace(',', '.')
    else:
        text = text.replace(',', '')
    if not re.fullmatch(r'[+-]?\d+(?:\.\d{1,2})?', text):
        raise ValueError('Invalid amount; choose the correct decimal separator.')
    value = Decimal(text)
    if not value.is_finite():
        raise ValueError('Amount must be finite.')
    return value


def day(value, date_format):
    try:
        return datetime.strptime(str(value).strip(), date_format).date().isoformat()
    except (ValueError, TypeError):
        raise ValueError('Invalid transaction date for the selected format.') from None


ALIASES = {
    'date': ('date', 'transaction date', 'posting date', 'value date'),
    'description': ('description', 'details', 'narrative', 'transaction description'),
    'reference': ('reference', 'payment reference', 'beneficiary reference', 'invoice number'),
    'name': ('name', 'payer', 'payer name', 'client', 'customer'),
    'amount': ('amount', 'transaction amount'),
    'credit': ('credit', 'credits', 'deposit', 'money in', 'credit amount'),
    'debit': ('debit', 'debits', 'withdrawal', 'money out', 'debit amount'),
    'id': ('transaction id', 'transaction number', 'bank transaction id'),
    'currency': ('currency', 'currency code'),
}


def parse_statement(content, options):
    if not content or len(content.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Upload a nonempty CSV of at most 2 MB.')
    if not isinstance(options, dict) or not isinstance(options.get('columns', {}), dict):
        raise ValueError('Options and column mappings must be objects.')
    date_format = options.get('date_format', '%Y-%m-%d')
    if date_format not in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y'):
        raise ValueError('Choose a supported date format.')
    separator = options.get('decimal_separator', '.')
    if separator not in ('.', ','):
        raise ValueError('Choose a decimal separator.')
    delimiter = options.get('delimiter', 'auto')
    if delimiter == 'auto':
        try:
            delimiter = csv.Sniffer().sniff(content[:8192], delimiters=',;\t').delimiter
        except csv.Error:
            raise ValueError('Cannot detect columns. Choose comma, semicolon or tab.') from None
    if delimiter not in (',', ';', '\t'):
        raise ValueError('Invalid delimiter.')
    reader = csv.reader(io.StringIO(content.lstrip('\ufeff')), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError('CSV header required.') from None
    if len(header) > 100 or len(set(map(normalized, header))) != len(header):
        raise ValueError('CSV needs unique column headings (maximum 100).')
    headers = {normalized(h): i for i, h in enumerate(header)}
    columns = {}
    for field, aliases in ALIASES.items():
        requested = options.get('columns', {}).get(field)
        matches = [headers[normalized(requested)]] if requested and normalized(requested) in headers else [headers[a] for a in aliases if a in headers]
        if requested and normalized(requested) not in headers:
            raise ValueError('Mapped column is absent: ' + field)
        if len(set(matches)) > 1:
            raise ValueError('Multiple columns for ' + field + '; specify a column mapping.')
        if matches:
            columns[field] = matches[0]
    if 'date' not in columns or not any(k in columns for k in ('amount', 'credit')):
        raise ValueError('CSV requires Date and Amount or Credit columns. Export with a header row.')
    if not any(k in columns for k in ('name', 'reference', 'description')):
        raise ValueError('CSV requires a payer name, reference or description column.')
    rows, issues = [], []
    for number, cells in enumerate(reader, 2):
        if number > MAX_ROWS + 1:
            raise ValueError('Maximum 5,000 transactions per upload.')
        if not any(c.strip() for c in cells):
            continue
        try:
            if len(cells) != len(header):
                raise ValueError('Column count does not match header.')
            get = lambda k: cells[columns[k]].strip() if k in columns else ''
            if get('currency') and get('currency').upper() != 'ZAR':
                raise ValueError('Only ZAR transactions are supported.')
            tx_date = day(get('date'), date_format)
            if 'credit' in columns:
                credit = amount(get('credit') or '0', separator)
                debit = amount(get('debit') or '0', separator)
                if credit < 0 or debit < 0 or credit and debit:
                    raise ValueError('Credit/debit columns require nonnegative values and only one side per row.')
                value = credit - debit
            else:
                value = amount(get('amount'), separator)
            if max(map(len, cells), default=0) > 2000:
                raise ValueError('Field exceeds 2,000 characters.')
            rows.append({'row': number, 'date': tx_date, 'amount': str(value), 'description': get('description'),
                         'reference': get('reference'), 'name': get('name'), 'transaction_id': get('id')})
        except (ValueError, InvalidOperation) as error:
            issues.append({'row': number, 'reason': str(error)})
    if not rows:
        raise ValueError('No valid transactions. Check date, amount and column settings.')
    return rows, issues


def reconcile(rows, invoices, payments, start, end, issues=()):
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    if start > end:
        raise ValueError('Statement start must be on or before its end.')
    valid = []
    excluded = []
    for inv in invoices:
        try:
            if inv.get('status') not in ('sent', 'overdue', 'paid', 'partially_paid', 'unpaid'):
                continue
            if inv.get('currency_code') != 'ZAR':
                excluded.append({'invoice_id': str(inv.get('invoice_id', '')), 'reason': 'Currency is not verified ZAR.'}); continue
            total = Decimal(str(inv['total'])); balance = Decimal(str(inv['balance']))
            issued = date.fromisoformat(inv['date']); due = date.fromisoformat(inv['due_date'])
            if not total.is_finite() or not balance.is_finite() or total <= 0 or balance < 0 or balance > total:
                raise ValueError('Invalid invoice amounts.')
            if issued > end:
                continue
            valid.append({**inv, 'invoice_id': str(inv['invoice_id']), 'customer_id': str(inv['customer_id']),
                          'total': str(total), 'balance': str(balance), 'date': issued.isoformat(), 'due_date': due.isoformat()})
        except (KeyError, ValueError, InvalidOperation):
            excluded.append({'invoice_id': str(inv.get('invoice_id', '')), 'reason': 'Incomplete or invalid invoice data.'})
    numbers = {}
    for inv in valid:
        if normalized(inv.get('invoice_number', '')):
            numbers.setdefault(normalized(inv['invoice_number']), []).append(inv)
    def identity(row):
        return ('id', row['transaction_id']) if row['transaction_id'] else (row['date'], row['amount'], normalized(row['reference']), normalized(row['name']), normalized(row['description']))
    counts = Counter(identity(row) for row in rows)
    results = []; allocated = Counter(); tentative = Counter()
    for row in rows:
        result = {**row, 'candidates': [], 'status': 'unmatched', 'reason': 'No unique reference or exact client-name match.'}
        value = Decimal(row['amount'])
        if not start.isoformat() <= row['date'] <= end.isoformat():
            result.update(status='out_of_period', reason='Outside the declared statement period.')
        elif value <= 0:
            result.update(status='ignored', reason='Debit or zero amount; not an incoming customer payment.')
        elif counts[identity(row)] > 1:
            result.update(status='duplicate_review', reason='Repeated transaction ID or identical row; verify whether these are separate credits.')
        else:
            text = ' ' + normalized(' '.join(row[k] for k in ('reference', 'description', 'name'))) + ' '
            refs = [inv for number, matches in numbers.items() if ' ' + number + ' ' in text for inv in matches]
            by_name = [inv for inv in valid if len(normalized(inv.get('customer_name', ''))) >= 4 and
                       ' ' + normalized(inv['customer_name']) + ' ' in text]
            basis = 'Invoice number' if refs else 'Exact client name'
            candidates = refs or by_name
            # Amount/date help disambiguate one client's invoices, never different clients.
            customer_ids = {inv['customer_id'] for inv in candidates}
            if len(customer_ids) == 1 and not refs:
                candidates = [inv for inv in candidates if inv['date'] <= row['date'] and
                              value in (Decimal(inv['total']), Decimal(inv['balance']))]
            result['candidates'] = [{'invoice_id': inv['invoice_id'], 'invoice_number': inv.get('invoice_number'), 'customer_id': inv['customer_id'],
                                     'customer_name': inv.get('customer_name'), 'total': inv['total'],
                                     'balance': inv['balance']} for inv in candidates[:20]]
            if len(candidates) != 1 or len(customer_ids) > 1:
                if candidates:
                    result.update(status='ambiguous', reason='Multiple invoices or clients match; owner review required.')
            else:
                inv = candidates[0]; iid = inv['invoice_id']
                if inv['date'] > row['date']:
                    result.update(status='review', reason='Payment predates invoice; verify deposit/prepayment.')
                elif allocated[iid] + value > Decimal(inv['total']):
                    result.update(status='review', reason='Credits exceed invoice total; allocation needs review.')
                else:
                    allocated[iid] += value
                    existing = [p for p in payments if str(p.get('customer_id')) == inv['customer_id'] and
                                p.get('date') == row['date'] and normalized(p.get('reference_number', '')) and
                                normalized(p['reference_number']) == normalized(row['reference']) and
                                str(p.get('amount')) and Decimal(str(p['amount'])) == value]
                    if len(existing) == 1:
                        result.update(status='recorded_match', reason=basis + '; same customer, date, amount and reference already recorded in Zoho.')
                    else:
                        tentative[iid] += value
                        result.update(status='suggested_match', reason=basis + '; review bank receipt and ledger allocation before recording payment.')
                    result['invoice_id'] = iid
        results.append(result)
    outstanding = []
    for inv in valid:
        balance = Decimal(inv['balance'])
        if balance <= 0 or inv['due_date'] > end.isoformat():
            continue
        evidence = tentative[inv['invoice_id']]
        outstanding.append({'invoice_id': inv['invoice_id'], 'invoice_number': inv.get('invoice_number'),
                            'customer_name': inv.get('customer_name'), 'due_date': inv['due_date'],
                            'ledger_balance': str(balance), 'suggested_bank_credits': str(evidence),
                            'unexplained_balance': str(max(Decimal('0'), balance - evidence)),
                            'status': 'Review bank credits / ledger' if evidence else 'No matched credit in this statement'})
    return {'period_start': start.isoformat(), 'period_end': end.isoformat(), 'currency': 'ZAR',
            'created_at': datetime.now(timezone.utc).isoformat(), 'transactions': results, 'outstanding': outstanding,
            'parse_issues': list(issues), 'excluded_invoices': excluded,
            'invoice_options': [{'invoice_id': i['invoice_id'], 'invoice_number': i.get('invoice_number'), 'customer_id': i['customer_id'], 'customer_name': i.get('customer_name'), 'date': i['date'], 'total': i['total'], 'balance': i['balance']} for i in valid],
            'warning': 'Potential missing payments only: this statement may omit other accounts, cash, deposits or earlier payments. Current Zoho balances are not historical balances. Suggested matches do not record payments or authorize reminders.'}


def markdown_report(report):
    def safe(value):
        return str(value or '').replace('<', '&lt;').replace('>', '&gt;').replace('|', '/').replace('\r', ' ').replace('\n', ' ')
    lines = ['# RG Midrand payment reconciliation', '', f"Statement period: {report['period_start']} to {report['period_end']} · ZAR", '', report['warning'], '',
             '## Outstanding invoices requiring review', '', '| Client | Invoice | Due | Current ledger balance | Suggested credits | Unexplained |', '|---|---|---|---:|---:|---:|']
    for inv in report['outstanding']:
        lines.append('| ' + ' | '.join(safe(inv[k]) for k in ('customer_name', 'invoice_number', 'due_date', 'ledger_balance', 'suggested_bank_credits', 'unexplained_balance')) + ' |')
    if not report['outstanding']:
        lines += ['', 'No overdue outstanding invoices found in the supplied current ledger snapshot.']
    lines += ['', '## Statement transactions', '', '| Row | Date | Credit / debit | Reference / payer | Result | Reason |', '|---|---|---:|---|---|---|']
    for row in report['transactions']:
        lines.append('| ' + ' | '.join(safe(x) for x in (row['row'], row['date'], row['amount'], row['reference'] or row['name'] or row['description'], row['status'], row['reason'])) + ' |')
    lines += ['', '## Import / coverage issues', '']
    lines += [f"- Row {x['row']}: {safe(x['reason'])}" for x in report['parse_issues']]
    lines += [f"- Invoice {safe(x['invoice_id'])}: {safe(x['reason'])}" for x in report['excluded_invoices']]
    return '\n'.join(lines) + '\n'
