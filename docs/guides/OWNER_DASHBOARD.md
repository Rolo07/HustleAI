# RG Midrand owner dashboard

The dashboard is part of the `hustleai` Python package. It reuses `Service` for
customer lookup, eligible last orders, invoice validation, draft PDF review and
approval; `WhatsAppClient`, signed webhook parsing and the HTTP gateway for Meta;
and the existing Hermes chat-completions adapter. No JavaScript framework or new
Python dependency is required. All Meta and Hermes secrets remain server-side.

## Local preview

```sh
.venv/bin/python -m hustleai.dashboard.server --demo
```

Open http://127.0.0.1:8090. Demo is explicitly fictional, persists in the ignored
`var/dashboard-demo/journal.sqlite3`, and makes no provider requests. It exercises
the same state machine and owner actions as live mode, using fictional catalog,
customer and draft adapters. It is never selected automatically on a live error.

Use the demo-only controls to send customer messages and confirm the current order.
Editing quantities or adding/removing catalog products creates a new version,
recalculates the total and revokes confirmation/approval. A customer can confirm
only by `CONFIRM <exact-version>` from the authenticated conversation. Free-text
changes are escalated rather than guessed. Owner edits remain proposals.

The inbox has search, unread counts, Needs approval / Hermes active / Human takeover
filters, customer/agent/owner messages, and separate internal notes. Review queue,
orders and activity views share the same persistent journal. View order jumps to
the order panel on narrow screens. Controls expose errors; polling never swaps in
sample data after a live failure.

## Live test-account mode

This implementation intentionally permits only the provided test Phone Number ID
`1417138624811816` and verified recipient `+27837758811`. It does not register the
real business number, publish Meta's app, add billing or change permissions.

1. Keep the existing Zoho/tenant/Supabase credentials in their private runtime paths.
   `--root` selects the tenant data directory. Live mode opens the existing TLS
   PostgreSQL session pooler and refuses SQLite or sample fallback.
2. Review and apply the additive `202610110007_owner_dashboard.sql` migration with
   the existing upgrade tool once Supabase is reachable. It adds one private
   dashboard journal table, grants the existing tenant group access and installs
   the existing organization RLS policy. No customer/order data is migrated.

   ```sh
   .venv/bin/python -m hustleai.storage.postgres.upgrade
   ```

3. Enter Meta credentials using the existing hidden-prompt test setup if needed.
   Never paste secrets into chat or browser code.

   ```sh
   .venv/bin/python -m hustleai.integrations.whatsapp.test_assistant configure
   .venv/bin/python -m hustleai.dashboard.server --live --configure-owner
   .venv/bin/python -m hustleai.dashboard.server --live
   ```

4. Sign in on localhost with the locally configured owner password. Browser sessions
   are signed, expire after eight hours, use HttpOnly/SameSite=Strict cookies and
   are invalidated on restart. State, catalog, PDFs and actions require owner
   authentication. Mutations require matching Origin and Host. Password entry is
   rate-limited. Dashboard routes accept localhost hosts only, so a webhook tunnel
   does not expose the owner UI. The static shell contains no customer data.
5. Point a **webhook-only** HTTPS tunnel/reverse proxy at this server's `/webhook`
   and `/health`. Callback verification uses the server's environment verify token;
   POST verifies the exact raw-body Meta HMAC. Subscribe `messages` and verify
   HustleAI's test-WABA subscription. Route this test number to only this worker;
   the earlier `test_assistant` echo server must not independently handle its
   messages because it has no dashboard takeover state.

Current temporary HTTPS callback (expires when the tunnel stops):
`https://cells-metropolitan-daniel-strategy.trycloudflare.com/webhook`.
Use the existing `WHATSAPP_VERIFY_TOKEN` from local `.env` in Meta's callback
configuration, then subscribe the `messages` field. Do not put the app secret or
access token in the callback form. The earlier echo server has been stopped.
HTTPS verification returned the correct challenge; signed empty POST returned 200,
bad signatures returned 403, and the public owner route returned 403. These are
transport checks, not a genuine Meta inbound/customer-confirmation event.

The owner UI is local-only. Do not expose it on the internet as-is. A VPS deployment
needs an explicitly configured HTTPS owner entry point, Secure cookies, trusted
origins and host allowlists. No public hosting was performed.

## Hermes on the VPS

Follow [the VPS customer-profile guide](HERMES_CUSTOMER_VPS.md). It creates a
separate tool-free profile and uses a private SSH tunnel; the owner profile is
never reused. The local hidden-prompt helper verifies `/v1/toolsets` before saving
credentials. The adapter repeats that check before transmitting customer text.
Every generated enquiry reply still needs owner approval. If Hermes is unavailable
or its policy cannot be verified, enquiries escalate; deterministic reorder
validation remains available when Zoho/storage are connected.

## Reorder and dispatch guarantees

- Unique current customer lookup by verified WhatsApp number; ambiguous/missing
  records escalate without sending another customer's details.
- Reuse eligible latest order selection from `invoice_review.py` (issued invoices,
  no drafts/voids/tests, tied latest dates require resolution).
- Read current catalog rates and stock. Missing/insufficient availability, inactive
  products, historical discounts/charges and unknown tax treatment escalate.
  This first adapter supports explicitly non-VAT-registered tenants; VAT-inclusive
  price configuration must be added before VAT-registered automation is enabled.
- Customer confirmation binds the exact version, recipient and customer. Only
  then is an unsent ZAR draft created with seven-day terms. Final Zoho totals must
  match the confirmed total. A unique version reference permits reconciliation.
- The reviewed PDF is accessible only to the owner. Approval revalidates the live
  invoice fingerprint, recipient mapping and PDF hash/expiry using existing
  workflow helpers, and sends that file once. Editing invalidates reviews and
  customer confirmation. Rejection records the reason and keeps the draft unsent.
- Owner action request IDs are content-bound. Proposal and invoice outbox keys are
  version-bound. Webhook IDs remain deduplicated after restarts.
- Takeover and dispatch share a PostgreSQL session advisory lock. The last state
  check and actual remote send occur while holding it. A takeover can wait for an
  already-in-flight send to finish; once takeover returns, no queued automated
  send may start. Queued Hermes replies are cancelled, and generation results
  produced during takeover are discarded. Resume is explicit.
- Queue states distinguish queued, attempting send, API accepted, Meta sent,
  delivered, read, failed and cancelled. Meta acceptance is not delivery. Status
  events update only messages with the matching provider ID and never send replies.
- Attempt markers commit before provider mutations. Timeouts/crashes never trigger
  blind draft or message retries. Draft reconciliation reads the exact reference,
  recipient, lines, prices, currency, total and due date before restoring review.
  Missing/ambiguous results require manual provider reconciliation. Unknown send
  outcomes cannot be automatically reconciled without a provider ID; no resend
  control is provided. Refresh PDF review handles expired reviews of unchanged drafts.
- Free-form messages wait for a signed inbound event opening the 24-hour window.
  The dashboard does not invent inbound activity or send a template to bypass it.

This initial journal uses one locked JSON state per organization for consistent
transitions. It favors correctness over high throughput. Run one active dashboard
worker per tenant: startup recovery must not overlap a currently processing worker.
Keep the complete audit/history protected with database backups; retention and
archival policies need a business decision before production scale.

## Current integration blockers (October 11, 2026)

- Supabase became reachable again. The additive dashboard migration was applied;
  authenticated live startup and tenant journal connectivity succeeded at port 8091.
- Hermes is on the VPS; its restricted profile URL and server-side API key are not
  yet configured locally. The owner profile is not reused for customers.
- Meta's actual HustleAI dashboard states unpublished apps receive dashboard test
  webhooks only, not real inbound data even from admins/testers. Publishing remains
  unauthorized. The last read-only check also found HustleAI not subscribed to the
  test WABA. Meta dashboard callback/subscription testing remains pending; the HTTPS
  callback itself passed direct verification and HMAC checks.
- Authenticated live catalog reads succeeded: 23 active products, none with
  authoritative stock fields. Automated reorders therefore escalate until an
  authoritative availability source is connected. Prices are never guessed.
- Outbound `hello_world` was previously accepted through the real Python client
  and confirmed on Roland's phone. This proves transport only, not this dashboard's
  complete live reorder/PDF journey. No real customer draft was created here.

## Verification

```sh
.venv/bin/python -m unittest tests.unit.test_dashboard -v
.venv/bin/python -m unittest discover -s tests -q
node --check src/hustleai/dashboard/static/app.js
```

Tests cover owner authentication and CSRF, current-version approval, changed-order
confirmation invalidation, repeated confirmations/approvals, takeover and Hermes
race handling, internal-note isolation, verified-recipient-only live events,
status correlation/ordering, no-replay provider failures and interrupted-operation
recovery. Browser QA checked editing/recalculation, approvals, manual takeover,
search and narrow-screen layout. Live provider mutations are not exercised by these fictional-data tests.

Latest validation: 157 tests run, 121 passed, 36 skipped; JavaScript syntax and
Git whitespace checks passed. Live login/state and catalog GETs returned 200.
Browser QA exercised fictional order editing, confirmation, approval and manual
reply, and verified the 390px breakpoint without horizontal overflow. No live
order or dashboard customer message was created/sent during these checks.
