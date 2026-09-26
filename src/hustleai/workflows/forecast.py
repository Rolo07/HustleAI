"""Weekly reorder forecast for stock ordering and delivery planning.

Predicts each customer's next order from their last three order dates, or from
a cycle Roland set for that customer. Read-only in Zoho: it never creates
drafts, contacts customers or changes invoices. Reports are saved in storage
so the weekly job and the owner MCP tool return the same content.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from hustleai.domain.phones import normalize
from hustleai.domain.validation import identifier
from hustleai.workflows.invoice_review import eligible_order

TIMEZONE = ZoneInfo('Africa/Johannesburg')
LEAD_DAYS = 7          # Report starts this many days after the run date.
WINDOW_DAYS = 7        # One week of expected orders per report.
HISTORY_ORDERS = 3     # Most recent distinct order dates used for prediction.
REUSE_DAYS = 7         # A saved report is reused for on-demand requests within a week.


def local_today(now=None):
    """Return today's date in South Africa, whatever the server timezone is."""
    return (now or datetime.now(timezone.utc)).astimezone(TIMEZONE).date()


def predict(order_dates, override_days=None):
    """Predict the next order date from past order dates.

    Args:
        order_dates: Dates of eligible past orders. Same-day invoices count
            as one order.
        override_days: Owner-set cycle in days; replaces the history average.
    Returns:
        Dict with next_date, last_order, cycle_days, basis, confidence and
        the dates used, or None when history is too short to predict.

    Uses the three most recent distinct dates: the cycle is the rounded mean
    of the gaps between them. Confidence is low with only one gap, or when
    the gaps differ by more than half the average.
    """
    dates = sorted(set(order_dates), reverse=True)[:HISTORY_ORDERS]
    if not dates:
        return None
    last = dates[0]
    if override_days:
        return {'next_date': last + timedelta(days=int(override_days)), 'last_order': last,
                'cycle_days': int(override_days), 'basis': 'owner', 'confidence': 'set by owner',
                'dates_used': dates}
    if len(dates) < 2:
        return None
    gaps = [(newer - older).days for newer, older in zip(dates, dates[1:])]
    average = Decimal(sum(gaps)) / len(gaps)
    cycle = max(1, int(average.quantize(Decimal(1), ROUND_HALF_UP)))
    low = len(gaps) == 1 or Decimal(max(gaps) - min(gaps)) > average / 2
    return {'next_date': last + timedelta(days=cycle), 'last_order': last, 'cycle_days': cycle,
            'basis': 'history', 'confidence': 'low' if low else 'normal', 'dates_used': dates}


def classify(prediction, today):
    """Return 'due' for the 7-14 day window, 'overdue' if past, else None."""
    start = today + timedelta(days=LEAD_DAYS)
    end = start + timedelta(days=WINDOW_DAYS)
    if start <= prediction['next_date'] < end:
        return 'due'
    if prediction['next_date'] < today:
        return 'overdue'
    return None


def number(value):
    """Format a Decimal without trailing zeros, for quantities."""
    text = format(value.quantize(Decimal('0.01'), ROUND_HALF_UP), 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def money(value):
    """Format a Decimal as a two-decimal rand amount string."""
    return format(value.quantize(Decimal('0.01'), ROUND_HALF_UP), 'f')


def average_products(invoices, order_count):
    """Average quantity per product per order across the used invoices.

    Args:
        invoices: Full invoices for the order dates used in the prediction.
        order_count: Number of distinct order dates, the averaging divisor.
    Returns:
        List of {key, item_id, name, quantity(Decimal)} sorted by name.
    """
    totals = {}
    for invoice in invoices:
        for line in invoice.get('line_items', []):
            item_id = str(line.get('item_id') or '')
            name = str(line.get('name') or line.get('description') or 'Unnamed item').strip()
            key = item_id or name.casefold()
            entry = totals.setdefault(key, {'key': key, 'item_id': item_id, 'name': name, 'quantity': Decimal(0)})
            entry['quantity'] += Decimal(str(line.get('quantity') or 0))
    for entry in totals.values():
        entry['quantity'] /= order_count
    return sorted(totals.values(), key=lambda e: e['name'].casefold())


def total_products(customers):
    """Sum each product's expected quantity across all due customers."""
    totals = {}
    for customer in customers:
        for product in customer['products']:
            entry = totals.setdefault(product['key'], {'item_id': product['item_id'], 'name': product['name'],
                                                       'quantity': Decimal(0), 'customers': 0})
            entry['quantity'] += Decimal(product['quantity'])
            entry['customers'] += 1
    return [dict(e, quantity=number(e['quantity']))
            for e in sorted(totals.values(), key=lambda e: e['name'].casefold())]


def address_area(contact):
    """Return (area, suburb) from the shipping address, else billing address."""
    for field in ('shipping_address', 'billing_address'):
        address = contact.get(field) or {}
        city = str(address.get('city') or '').strip()
        suburb = str(address.get('street2') or '').strip()
        if city or suburb:
            return (city or suburb).title(), (suburb.title() if city else '')
    return 'No address', ''


def group_by_area(customers):
    """Group due customers by delivery area, with missing addresses last."""
    groups = defaultdict(list)
    for customer in customers:
        groups[customer['area']].append({'customer_name': customer['customer_name'],
                                         'suburb': customer['suburb'],
                                         'predicted_date': customer['predicted_date']})
    order = sorted(groups, key=lambda area: (area == 'No address', area.casefold()))
    return [{'area': area, 'customers': sorted(groups[area], key=lambda c: c['predicted_date'])}
            for area in order]


def contact_phone(contact, country):
    """Return the first normalized mobile or phone number on a contact."""
    for source in [contact] + contact.get('contact_persons', []):
        for field in ('mobile', 'phone'):
            value = normalize(source.get(field, ''), country)
            if value:
                return value
    return ''


def render_markdown(report):
    """Render a saved forecast as a short private Markdown report."""
    last_day = date.fromisoformat(report['window_end']) - timedelta(days=1)
    lines = [f"# Reorder forecast: {report['window_start']} to {last_day.isoformat()}", '',
             f"Run on {report['run_date']} ({report['timezone']}). Predictions use each customer's "
             f"last {HISTORY_ORDERS} orders unless you set a cycle for them.", '']
    due = report['due']
    lines += [f'## Expected to order ({len(due)})', '']
    if due:
        lines += ['| Customer | Phone | Expected | Area | Last order | Cycle | Confidence | Est. value |',
                  '| --- | --- | --- | --- | --- | --- | --- | --- |']
        for c in due:
            area = c['area'] + (f", {c['suburb']}" if c['suburb'] else '')
            cycle = f"{c['cycle_days']} days" + (' (set by you)' if c['basis'] == 'owner' else '')
            lines.append(f"| {c['customer_name']} | {c['phone']} | {c['predicted_date']} | {area} | "
                         f"{c['last_order_date']} | {cycle} | {c['confidence']} | R{c['expected_value']} |")
        lines += ['', f"Estimated total: R{report['expected_value_total']}. This is the average of past "
                  'invoices, not current prices.']
    else:
        lines.append('No customers are expected to order in this week.')
    lines += ['', '## Stock to order', '']
    if report['products']:
        lines += ['| Product | Expected quantity | Customers |', '| --- | --- | --- |']
        lines += [f"| {p['name']} | {p['quantity']} | {p['customers']} |" for p in report['products']]
    else:
        lines.append('Nothing expected.')
    lines += ['', '## Deliveries by area', '']
    for group in report['areas']:
        names = ', '.join(c['customer_name'] + (f" ({c['suburb']})" if c['suburb'] else '')
                          + f" {c['predicted_date']}" for c in group['customers'])
        lines.append(f"- **{group['area']}** ({len(group['customers'])}): {names}")
    if not report['areas']:
        lines.append('No deliveries expected.')
    overdue = report['overdue']
    lines += ['', f'## Overdue ({len(overdue)})', '']
    if overdue:
        lines += ['| Customer | Phone | Was expected | Days overdue | Last order |', '| --- | --- | --- | --- | --- |']
        lines += [f"| {c['customer_name']} | {c['phone']} | {c['predicted_date']} | {c['days_overdue']} | "
                  f"{c['last_order_date']} |" for c in overdue]
    else:
        lines.append('No overdue customers.')
    unpredictable = report['unpredictable']
    lines += ['', f'## Unable to predict ({len(unpredictable)})', '']
    if unpredictable:
        lines.append('These customers have only one order. Set a cycle for them to include them.')
        lines.append('')
        lines += [f"- {c['customer_name']}, last order {c['last_order_date']}" for c in unpredictable]
    else:
        lines.append('None.')
    if report['excluded_customers']:
        lines += ['', f"{report['excluded_customers']} customer(s) are excluded from forecasts by you."]
    return '\n'.join(lines) + '\n'


class ForecastWorkflows:
    """Mixin using Service's API, storage, config and client lookup."""

    def reorder_forecast(self, refresh=False, today=None, exact_date=False):
        """Return this week's saved forecast, or build and save a new one.

        Args:
            refresh: Rebuild from Zoho even when a recent report exists.
            today: Run date; defaults to today in Africa/Johannesburg.
            exact_date: Reuse only a report saved for this exact run date. The
                scheduled job uses this so each Monday gets its own report;
                on-demand requests reuse any report from the last seven days.
        Returns:
            Report dictionary; see build_forecast.
        Raises:
            ValueError: Zoho reads fail or the GET budget is reached.
        """
        today = today or local_today()
        if not refresh and exact_date:
            saved = self.store.forecast(today)
            if saved:
                return saved
        elif not refresh:
            latest = self.store.forecast()
            if latest and 0 <= (today - date.fromisoformat(latest['run_date'])).days < REUSE_DAYS:
                return latest
        return self.build_forecast(today)

    def build_forecast(self, today=None):
        """Build the forecast from Zoho invoices and save it for this run date.

        Reads one paged list of all invoices, then full invoices and contact
        details only for customers who are due or overdue. Customers Roland
        excluded are skipped. Writes only the saved report in storage.
        """
        today = today or local_today()
        excluded_ids = set(map(str, self.workflow_config.get('test_invoice_ids', [])))
        settings = self.store.order_cycles()
        by_customer = defaultdict(list)
        for summary in self.api.pages('invoices', 'invoices'):
            if eligible_order(summary, excluded_ids) and date.fromisoformat(summary['date']) <= today:
                by_customer[str(summary['customer_id'])].append(summary)
        due, overdue, unpredictable = [], [], []
        excluded_customers = 0
        for customer_id, summaries in sorted(by_customer.items()):
            setting = settings.get(customer_id, {})
            if setting.get('excluded'):
                excluded_customers += 1
                continue
            result = self.forecast_customer(summaries, setting.get('cycle_days'), today, excluded_ids)
            if result is None:
                continue
            status, prediction, invoices = result
            name = summaries[0].get('customer_name') or customer_id
            if status == 'unpredictable':
                unpredictable.append({'contact_id': customer_id, 'customer_name': name,
                                      'last_order_date': prediction.isoformat()})
                continue
            contact = self.api.get('contacts/' + identifier(customer_id))['contact']
            entry = {'contact_id': customer_id, 'customer_name': contact.get('contact_name') or name,
                     'phone': contact_phone(contact, self.country),
                     'predicted_date': prediction['next_date'].isoformat(),
                     'last_order_date': prediction['last_order'].isoformat(),
                     'cycle_days': prediction['cycle_days'], 'basis': prediction['basis'],
                     'confidence': prediction['confidence']}
            if status == 'overdue':
                entry['days_overdue'] = (today - prediction['next_date']).days
                overdue.append(entry)
                continue
            orders = len(prediction['dates_used'])
            value = sum((Decimal(str(i.get('total') or 0)) for i in invoices), Decimal(0)) / orders
            area, suburb = address_area(contact)
            products = average_products(invoices, orders)
            entry.update(area=area, suburb=suburb, orders_used=orders, expected_value=money(value),
                         products=[{'key': p['key'], 'item_id': p['item_id'], 'name': p['name'],
                                    'quantity': number(p['quantity'])} for p in products])
            due.append(entry)
        due.sort(key=lambda c: (c['predicted_date'], c['customer_name'].casefold()))
        overdue.sort(key=lambda c: (c['days_overdue'], c['customer_name'].casefold()))
        unpredictable.sort(key=lambda c: c['customer_name'].casefold())
        start = today + timedelta(days=LEAD_DAYS)
        end = start + timedelta(days=WINDOW_DAYS)
        report = {'run_date': today.isoformat(), 'window_start': start.isoformat(), 'window_end': end.isoformat(),
                  'timezone': str(TIMEZONE), 'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                  'due': due, 'products': total_products(due), 'areas': group_by_area(due),
                  'overdue': overdue, 'unpredictable': unpredictable,
                  'expected_value_total': money(sum((Decimal(c['expected_value']) for c in due), Decimal(0))),
                  'excluded_customers': excluded_customers,
                  'note': 'Estimates from order history. Nothing was sent and Zoho was not changed.'}
        self.store.save_forecast(today, start, end, report)
        return report

    def forecast_customer(self, summaries, override_days, today, excluded_ids):
        """Predict one customer, rechecking full invoices only when it matters.

        Returns ('unpredictable', last_order_date, []), (status, prediction,
        full_invoices) for due/overdue customers, or None. A used invoice that
        fails the full TEST-marker check is dropped and the prediction redone.
        """
        pool = list(summaries)
        checked = {}
        while pool:
            prediction = predict([date.fromisoformat(s['date']) for s in pool], override_days)
            if prediction is None:
                return 'unpredictable', max(date.fromisoformat(s['date']) for s in pool), []
            status = classify(prediction, today)
            if status is None:
                return None
            used = [s for s in pool if date.fromisoformat(s['date']) in prediction['dates_used']]
            rejected = False
            for summary in used:
                invoice_id = identifier(summary['invoice_id'])
                if invoice_id not in checked:
                    full = self.api.get('invoices/' + invoice_id)['invoice']
                    checked[invoice_id] = full if eligible_order(full, excluded_ids) else None
                if checked[invoice_id] is None:
                    pool.remove(summary)
                    rejected = True
            if not rejected:
                return status, prediction, [checked[identifier(s['invoice_id'])] for s in used]
        return None

    def forecast_customer_id(self, phone, contact_id=''):
        """Resolve a phone to one client, returning (contact_id, name)."""
        customer = self.customer(phone, contact_id)
        return str(customer['contact_id']), customer.get('contact_name', '')

    def set_order_cycle(self, phone, cycle_days, contact_id=''):
        """Set a customer's reorder cycle, replacing the history prediction.

        Args:
            phone: Customer cellphone.
            cycle_days: Whole number of days between orders, 1 to 365.
            contact_id: Optional ID for a shared phone number.
        Returns: Saved setting. Writes storage only; Zoho is not changed.
        Raises: ValueError for an invalid cycle or ambiguous customer.
        """
        if isinstance(cycle_days, bool) or not str(cycle_days).strip().isdigit():
            raise ValueError('Cycle must be a whole number of days.')
        days = int(str(cycle_days).strip())
        if not 1 <= days <= 365:
            raise ValueError('Cycle must be between 1 and 365 days.')
        customer_id, name = self.forecast_customer_id(phone, contact_id)
        self.store.set_order_cycle(customer_id, days, name)
        return {'contact_id': customer_id, 'customer_name': name, 'cycle_days': days, 'excluded': False}

    def exclude_from_forecast(self, phone, exclude=True, contact_id=''):
        """Exclude a customer from forecasts, or include them again."""
        customer_id, name = self.forecast_customer_id(phone, contact_id)
        self.store.set_forecast_excluded(customer_id, bool(exclude), name)
        return {'contact_id': customer_id, 'customer_name': name, 'excluded': bool(exclude)}

    def clear_order_cycle(self, phone, contact_id=''):
        """Remove a customer's override and exclusion; history applies again."""
        customer_id, name = self.forecast_customer_id(phone, contact_id)
        self.store.clear_order_cycle(customer_id)
        return {'contact_id': customer_id, 'customer_name': name, 'cleared': True}
