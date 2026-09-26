"""Sync the Supabase orders copy from Zoho, or show when it last synced.

  hustleai-orders sync               Compare all invoices and repair the copy
  hustleai-orders sync --reserve 200 Leave more daily Zoho requests unused
  hustleai-orders status             Show the last sync and pending details

Run nightly at 22:00 South African time, before Zoho's daily request limit
resets at midnight. Reads Zoho only; never changes invoices.
"""
import argparse

from hustleai.workflows.orders import DEFAULT_RESERVE
from hustleai.workflows.service import Service


def describe(result):
    """One-line summary of a sync result."""
    left = result.get('zoho_requests_left')
    return (f"{result['listed']} invoices listed, {result['new_or_changed']} new or changed, "
            f"{result['deleted']} deleted, {result['details_fetched']} details read, "
            f"{result['details_pending']} details pending"
            + (f", {left} Zoho requests left today." if left is not None else '.'))


def main(argv=None):
    """Dispatch sync or status."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sync = sub.add_parser('sync', help='Sync the orders copy from Zoho')
    sync.add_argument('--reserve', type=int, default=DEFAULT_RESERVE,
                      help=f'Daily Zoho requests to leave unused (default {DEFAULT_RESERVE})')
    sub.add_parser('status', help='Show the last sync')
    args = parser.parse_args(argv)
    with Service() as service:
        if args.command == 'sync':
            print(describe(service.sync_orders(args.reserve)))
        else:
            synced_at, stale, result = service.orders_freshness()
            print(f"Last sync {synced_at} (UTC){' - more than two days old' if stale else ''}: {describe(result)}")


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
