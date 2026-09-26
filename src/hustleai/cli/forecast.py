"""Build the weekly reorder forecast or manage per-customer reorder cycles.

Examples:
  hustleai-forecast run                 Build today's report, or reuse today's saved one
  hustleai-forecast run --refresh       Rebuild from Zoho now
  hustleai-forecast run --date 2026-10-05
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

from hustleai.config import ROOT
from hustleai.storage.files import private_write
from hustleai.workflows.forecast import render_markdown
from hustleai.workflows.service import Service


def write_report(report, root=ROOT):
    """Save the Markdown report privately and return its path."""
    folder = root / 'reports'
    folder.mkdir(mode=0o700, exist_ok=True)
    path = folder / f"reorder-forecast-{report['run_date']}.md"
    private_write(path, render_markdown(report))
    return path


def main(argv=None):
    """Dispatch the run or cycles command and print a short result."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    run_parser = sub.add_parser('run', help='Build or reuse the weekly forecast')
    run_parser.add_argument('--date', type=date.fromisoformat, help='Run date YYYY-MM-DD; default is today in South Africa')
    run_parser.add_argument('--refresh', action='store_true', help='Rebuild even if a report exists for this week')
    run_parser.add_argument('--print', action='store_true', help='Also print the report')
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

    with Service() as service:
        if args.command == 'run':
            report = service.reorder_forecast(args.refresh, args.date, exact_date=True)
            path = write_report(report)
            print(f"{len(report['due'])} customer(s) expected {report['window_start']} to before {report['window_end']}; "
                  f"{len(report['overdue'])} overdue. Report: {path}")
            if args.print:
                print(render_markdown(report))
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
