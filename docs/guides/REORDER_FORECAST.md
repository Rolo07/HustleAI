# Weekly reorder forecast

**Status:** built and tested locally with a fake Zoho and a disposable database
schema. The live database migration and the first live run are still pending.

Every Monday at 07:00 South African time, the forecast lists customers expected to
order **7 to 14 days from now**. That gives Roland at least a week to order stock
and plan delivery routes. Hermes can also fetch the report whenever Roland asks.
The forecast reads Zoho only. It never creates drafts, contacts customers or
changes invoices.

## How a customer's next order is predicted

1. Past orders come from Zoho invoices. The eligibility rules are the same as for
   reorder retrieval: the status is sent, overdue, paid, partially paid or unpaid.
   Drafts, voids, configured `test_invoice_ids` and invoices marked TEST are
   ignored. Invoices dated after the run date are ignored.
2. Several invoices on the same day count as one order.
3. The prediction uses the **three most recent order dates**. The cycle is the
   average gap between them, rounded to whole days. The next order is the last
   order date plus that cycle.
4. If Roland set a cycle for a customer, that cycle replaces the history average.
5. Confidence is **low** when there are only two orders, or when the gaps differ
   by more than half the average.
6. A customer with only one order goes under "Unable to predict" until Roland
   sets a cycle for them.

"Today" is always the date in `Africa/Johannesburg`, so a server running in UTC
still uses the right day.

## What the report contains

| Section | Contents |
| --- | --- |
| Expected to order | Customers whose predicted date falls in the 7–14 day window, with phone, expected date, area, last order, cycle and confidence. |
| Stock to order | Average quantity per product over each customer's last three orders, totalled across expected customers. |
| Deliveries by area | Expected customers grouped by the city on their Zoho shipping address, or the billing address if there is none. The suburb comes from the second address line. |
| Overdue | Customers whose predicted date has passed with no newer order. |
| Unable to predict | Customers with only one order and no cycle set. |
| Estimated value | Average of each customer's last three invoice totals. This is based on past invoices, not current prices. |

Customers due within the next seven days are not listed again. They appeared in
last week's report.

## Commands

```sh
.venv/bin/hustleai-forecast run                    # build today's report, or reuse today's
.venv/bin/hustleai-forecast run --refresh --print  # rebuild now and print it
.venv/bin/hustleai-forecast cycles list
.venv/bin/hustleai-forecast cycles set 0821234567 21
.venv/bin/hustleai-forecast cycles exclude 0821234567   # stopped ordering
.venv/bin/hustleai-forecast cycles include 0821234567
.venv/bin/hustleai-forecast cycles clear 0821234567     # back to order history
```

Add `--contact-id` when several clients share one number. Each run saves the
report in Supabase and writes `reports/reorder-forecast-<date>.md` to the private
data directory. That folder is excluded from Git because it contains customer
names and phone numbers.

## Hermes tools

| Tool | Purpose |
| --- | --- |
| `reorder_forecast` | Returns the saved report from the last seven days, or builds one. `refresh=true` rebuilds it. Returns the report data and a Markdown version. |
| `set_reorder_cycle` | Sets a customer's cycle in days, from 1 to 365. Also includes an excluded customer again. |
| `exclude_from_forecast` | Excludes a customer, or includes them again with `exclude=false`. Any cycle already set is kept. |

These tools change only forecast settings. They skip the CONFIRM step because
nothing reaches customers or Zoho. They are owner-only, like every other tool
on this server.

## Storage

Migration `202609260003_reorder_forecast.sql` adds two private tables:

- `customer_order_cycles` holds one row per customer with a cycle, an exclusion
  flag and the customer name for readability.
- `reorder_forecasts` holds one saved report per run date. A rerun on the same
  date replaces that report, so a double scheduled run never creates duplicates.

Both tables use row-level security. The migration copies the runtime role's
organization rule from the `operations` table, so the organization ID never
appears in source control. Apply it with the upgrade command:

```sh
.venv/bin/python -m hustleai.storage.postgres.upgrade
```

## Schedule

Use exactly one of these:

- **VPS:** `deploy/schedule/hustleai-forecast.service` and `.timer`. The timer
  runs Mondays at 07:00 `Africa/Johannesburg` regardless of the server
  timezone, and catches up once after downtime.
- **Mac:** `deploy/schedule/com.hustleai.reorder-forecast.plist`. launchd uses
  the Mac's own timezone and runs only while the Mac is awake.

## Zoho request cost

One paged list of all invoices finds every customer's order dates. Full invoices
and contact details are fetched only for customers who are expected or overdue:
up to three invoices and one contact each. A run stops safely at the client's
limit of 800 read requests. The requests are spaced out, so a large run can take
a few minutes.

## Limits

- Predictions assume regular ordering. Seasonal or irregular customers are best
  given a cycle or excluded.
- Long-lapsed customers stay in the overdue list until Roland excludes them.
- The report is not sent over WhatsApp yet. Delivery can reuse the saved report
  once the WhatsApp gateway exists.
