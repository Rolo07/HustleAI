# PRD 09 — Weekly reorder forecast

[Open the visual flow guide](../flow-guide.html#09-reorder-forecast) · [Open full-size SVG](../diagrams/09-reorder-forecast.svg)

![PRD 09 — Weekly reorder forecast workflow](../diagrams/09-reorder-forecast.svg)

Status: implemented and tested locally. The live Supabase migration, first live
run and scheduler installation are pending. See [shared decisions](README.md)
and the [operating guide](../guides/REORDER_FORECAST.md).

## Goal

Give Roland at least a week's notice of which customers are likely to order, so
he can order stock from suppliers and plan delivery routes before orders arrive.

## Trigger and schedule

- Run every **Monday at 07:00 in `Africa/Johannesburg`**, whatever the server timezone.
- Roland can also ask Hermes for the forecast at any time.
- The report covers orders expected **7 to 14 days** after the run date. That
  is one week, starting one week out.
- The report goes privately to Roland. It is never sent to customers.

## Prediction rules

1. **Real orders only.** An order is an invoice with status sent, overdue, paid,
   partially paid or unpaid. Drafts, voids, configured test invoice IDs and
   invoices marked TEST are ignored, and so are invoices dated in the future.
   These are the same rules as reorder retrieval in [PRD 04](04-reorders.md).
2. **One order per day.** Several invoices on the same day count as one order.
3. **Last three orders.** The cycle is the average gap between the three most
   recent order dates, rounded to whole days. The next order is expected on the
   last order date plus that cycle.
4. **Roland's cycle wins.** If Roland set a cycle for a customer, it replaces
   the history average.
5. **Confidence.** It is low when a customer has only two orders, or when their
   gaps differ by more than half the average.
6. **Not enough history.** A customer with one order and no cycle is listed
   under "Unable to predict" and never guessed.
7. **Tracking start.** Only orders on or after `forecast_start_date` count. It
   is set when the app goes live on the VPS, because older Zoho history is
   incomplete. Until it is set, all history counts.
8. **Hidden test orders.** For customers who make the list, the full invoices
   are rechecked. A test order found in the line items is dropped and the
   prediction is redone.

## Choosing which customers appear

Every customer with order history is included automatically. Roland controls
the list with these settings:

| Roland wants to | Command | Hermes tool |
| --- | --- | --- |
| Include a one-order customer, or fix an irregular one | `hustleai-forecast cycles set <phone> <days>` | `set_reorder_cycle` |
| Remove a customer who stopped ordering | `hustleai-forecast cycles exclude <phone>` | `exclude_from_forecast` |
| Bring an excluded customer back | `hustleai-forecast cycles include <phone>` | `exclude_from_forecast` with `exclude=false` |
| Return to the history-based prediction | `hustleai-forecast cycles clear <phone>` | Not available; use the command |
| See current settings | `hustleai-forecast cycles list` | Not available; use the command |

- **Cycle range:** A cycle is a whole number of days from 1 to 365.
- **Shared numbers:** When several clients share a number, add `--contact-id`.
- **Exclusion:** Excluding a customer keeps any cycle already set. Setting a
  cycle includes an excluded customer again.
- **No confirmation step:** These settings skip the CONFIRM step because they
  change only the forecast, not Zoho or anything a customer sees.

## Report contents

| Section | Required information |
| --- | --- |
| Expected to order | Name, phone, expected date, delivery area, last order date, cycle, whether Roland set it, and confidence. |
| Stock to order | Average quantity per product over each customer's last three orders, totalled across expected customers. |
| Deliveries by area | Expected customers grouped by the city on their Zoho shipping address, or the billing address if there is none, with the suburb. Missing addresses are grouped last. |
| Overdue | Customers whose expected date has passed with no newer order, with days overdue. |
| Unable to predict | One-order customers without a cycle, with their last order date. |
| Estimated value | Average of each customer's last three invoice totals, plus the week's total. Labelled as history, not current pricing. |
| Excluded | Count of customers Roland excluded. |

Customers due within the next seven days are not repeated, because last week's
report listed them.

## State and recovery

- One saved report per organization and run date. A rerun on the same date
  replaces that report, so a doubled scheduled run never creates duplicates.
- On-demand requests reuse a report from the last seven days unless Roland asks
  for a refresh.
- The VPS timer catches up once after downtime. A missed run can also be rebuilt
  with `hustleai-forecast run --date <monday>`.
- A Zoho failure stops the run without saving a partial report. The previous
  report stays available.

## Acceptance criteria

These are covered by `tests/unit/test_forecast.py` and the opt-in PostgreSQL suite.

- Three orders 14 days apart predict the next order 14 days after the last one.
- A customer with one order is listed as unable to predict until a cycle is set.
- Roland's cycle replaces the history-based cycle.
- An excluded customer is never read from Zoho or listed.
- Expected dates exactly 7 days out are included; 14 days out are not.
- A date that is late Sunday in UTC but Monday in South Africa counts as Monday.
- Drafts, voids, future-dated and TEST invoices are ignored, including a TEST
  marker that appears only in line items.
- Product totals, area grouping and value totals match the used orders.
- Rerunning on the same date keeps one saved report.
- Orders before the tracking start date are ignored; an invalid date stops the run.
- Forecast settings and reports of one organization are invisible to another.

## Dependencies and open questions

| Item | Current position |
| --- | --- |
| Live migration `202609260003_reorder_forecast.sql` | Ready; apply with the upgrade command. |
| Scheduler | Mac launchd and VPS systemd files provided; install exactly one. |
| WhatsApp delivery of the report | Pending the business gateway ([PRD 01](01-message-routing.md)); it can reuse the saved report. |
| Long-lapsed customers | Stay in the overdue list until Roland excludes them. An automatic cut-off could be added later. |
| Seasonal or irregular customers | Handled by owner-set cycles; no seasonal model. |
| Only-selected-customers mode | Not built. The current design includes everyone with history. |
| Tracking start date | Decided 2026-09-27: count orders only from VPS go-live. Set with `hustleai-forecast start-date today` at deployment. |
