"""Build the weekly reorder forecast or manage per-customer reorder cycles.

Examples:
  hustleai-forecast run                 Build today's report, or reuse today's saved one
  hustleai-forecast run --refresh       Rebuild from Zoho now
  hustleai-forecast run --date 2026-10-05
  hustleai-forecast start-date today     Count only orders from go-live onward
  hustleai-forecast start-date          Show the tracking start date
  hustleai-forecast cycles list
  hustleai-forecast cycles set 0821234567 21
  hustleai-forecast cycles exclude 0821234567
  hustleai-forecast cycles include 0821234567
  hustleai-forecast cycles clear 0821234567

Read-only in Zoho. Reports are saved in storage and written privately to
reports/reorder-forecast-<date>.md in the data directory.
"""
import argparse
from datetime import date
import json

from hustleai.config import CONFIG, ROOT
from hustleai.storage.files import private_write
from hustleai.workflows.forecast import local_today, render_markdown, tracking_start
from hustleai.workflows.service import Service


def write_report(report, root=ROOT):
    """Save the Markdown report privately and return its path."""
    folder = root / 'reports'
    folder.mkdir(mode=0o700, exist_ok=True)
    path = folder / f"reorder-forecast-{report['run_date']}.md"
    private_write(path, render_markdown(report))
    return path


def start_date(value, clear=False):
    """Show, set or clear forecast_start_date in the private local settings.

    Needs no Zoho or database access. value is YYYY-MM-DD or 'today' in the
    tenant's timezone. Writes tenant.json when the folder has one (tenant
    folders have no .zoho-local.json), otherwise .zoho-local.json. Other
    settings in the file are preserved.
    """
    from hustleai.tenant import TENANT_FILE, Tenant, read_settings
    tenant_file = CONFIG.parent / TENANT_FILE
    target = tenant_file if tenant_file.exists() else CONFIG
    config = json.loads(target.read_text()) if target.exists() else {}
    zone = Tenant.from_settings(read_settings(CONFIG.parent, CONFIG)).zone
    if clear:
        config.pop('forecast_start_date', None)
    elif value:
        chosen = local_today(zone=zone) if value == 'today' else date.fromisoformat(value)
        config['forecast_start_date'] = chosen.isoformat()
    else:
        current = tracking_start(read_settings(CONFIG.parent, CONFIG))
        return f'Tracking orders since {current}.' if current else 'No start date set; all order history is counted.'
    private_write(target, json.dumps(config, indent=2) + '\n')
    if clear:
        return 'Start date cleared; all order history is counted.'
    return f"Tracking orders since {config['forecast_start_date']}. Older invoices are ignored."


def send_to_owner(service, report):
    """Send the report to the owner's WhatsApp once per run date."""
    from hustleai.integrations.whatsapp import config as wa_config
    from hustleai.integrations.whatsapp.client import WhatsAppClient
    from hustleai.workflows.forecast import render_whatsapp
    from hustleai.workflows.outbox import Outbox
    if not wa_config.configured(ROOT):
        return 'WhatsApp is not set up yet; the report was saved but not sent.'
    settings = wa_config.load(ROOT)
    rows = Outbox(service.store, WhatsAppClient(settings), settings, service.tenant.zone).send_text(
        'forecast', f"forecast:{report['run_date']}", settings['owner_number'], render_whatsapp(report))
    statuses = {row['status'] for row in rows}
    if statuses == {'waiting_window'}:
        return 'WhatsApp: held until the owner next messages the business number (24-hour rule).'
    return 'WhatsApp: ' + ', '.join(sorted(statuses))


def main(argv=None):
    """Dispatch the run or cycles command and print a short result."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    run_parser = sub.add_parser('run', help='Build or reuse the weekly forecast')
    run_parser.add_argument('--date', type=date.fromisoformat, help='Run date YYYY-MM-DD; default is today in South Africa')
    run_parser.add_argument('--refresh', action='store_true', help='Rebuild even if a report exists for this week')
    run_parser.add_argument('--print', action='store_true', help='Also print the report')
    run_parser.add_argument('--send', action='store_true', help="Also send it to the owner's WhatsApp")
    start = sub.add_parser('start-date', help='Show or set the first date of tracked orders')
    start.add_argument('value', nargs='?', help="YYYY-MM-DD or 'today'; omit to show the current date")
    start.add_argument('--clear', action='store_true', help='Count all order history again')
    cycles = sub.add_parser('cycles', help='Manage per-customer reorder cycles')
    actions = cycles.add_subparsers(dest='action', required=True)
    actions.add_parser('list')
    for name in ('set', 'exclude', 'include', 'clear'):
        action = actions.add_parser(name)
        action.add_argument('phone')
        if name == 'set':
            action.add_argument('days')
        action.add_argument('--contact-id', default='', help='Client ID when several share the number')
    args = parser.parse_args(argv)
    if args.command == 'start-date':
        print(start_date(args.value, args.clear))
        return

    with Service() as service:
        service.tenant.require('forecast')
        if args.command == 'run':
            report = service.reorder_forecast(args.refresh, args.date, exact_date=True)
            path = write_report(report)
            print(f"{len(report['due'])} customer(s) expected {report['window_start']} to before {report['window_end']}; "
                  f"{len(report['overdue'])} overdue. Report: {path}")
            if args.print:
                print(render_markdown(report))
            if args.send:
                print(send_to_owner(service, report))
        elif args.action == 'list':
            settings = service.store.order_cycles()
            if not settings:
                print('No customer cycles set; all forecasts use order history.')
            for contact_id, setting in settings.items():
                state = 'excluded' if setting['excluded'] else f"every {setting['cycle_days']} days"
                print(f"{contact_id}  {setting['note'] or ''}  {state}")
        elif args.action == 'set':
            result = service.set_order_cycle(args.phone, args.days, args.contact_id)
            print(f"{result['customer_name']}: reorders every {result['cycle_days']} days.")
        elif args.action in ('exclude', 'include'):
            result = service.exclude_from_forecast(args.phone, args.action == 'exclude', args.contact_id)
            print(f"{result['customer_name']}: {'excluded from' if result['excluded'] else 'included in'} forecasts.")
        else:
            result = service.clear_order_cycle(args.phone, args.contact_id)
            print(f"{result['customer_name']}: back to history-based prediction.")


def run():
    """Run the CLI and convert expected errors to concise terminal messages."""
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from None
    except KeyboardInterrupt:
        raise SystemExit('\nCancelled.') from None


if __name__ == '__main__':
    run()
