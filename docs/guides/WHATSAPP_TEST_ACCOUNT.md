# RG Midrand / HustleAI Meta test backend

This mode reuses the existing Python HTTP gateway, signature checks and Cloud API
client. It never loads Zoho, Hermes, tenant secrets or production Supabase. No new
Python packages are required. Only test phone ID `1417138624811816` and recipient
`+27837758811` are allowed. Business identity: RG Midrand; developer app: HustleAI
(`27773466375663340`); test WABA: `2319220545518996`.

## Credentials and startup

From the repository folder:

```sh
.venv/bin/python -m hustleai.integrations.whatsapp.test_assistant configure
.venv/bin/python -m hustleai.integrations.whatsapp.test_assistant check
.venv/bin/python -m hustleai.integrations.whatsapp.test_assistant serve
```

Configure uses hidden prompts for the Meta **test access token** and **app secret**
(App settings > Basic). It generates a random verification token and writes `.env`
with mode 0600. `.env` is excluded from Git. Do not paste credentials into chat or
shell command arguments. Existing environment variables override `.env`; this is
a literal KEY=value loader, not a shell script. `.env.example` lists the variables.
Set `WHATSAPP_API_VERSION` to a version supported by your dashboard (default
v23.0, matching the existing client). Expired test tokens must be replaced locally.
`check` only reads phone metadata and the WABA subscription; it sends no messages
and does not grant permissions or subscribe the app.

The backend listens on `127.0.0.1:8086`; `GET /health` returns `ok`.
The queue lives privately in `var/whatsapp-test/events.sqlite3`, excluded from Git.
One backend process should serve this test queue. Incoming text remains in the
private queue until processed; then it is removed. Logs never include tokens,
message bodies, names or recipient numbers.

## HTTPS and dashboard

With user-approved cloudflared installed:

```sh
cloudflared tunnel --url http://127.0.0.1:8086 --no-autoupdate
```

Use its generated HTTPS URL plus `/webhook` as the callback. Temporary tunnel URLs
change when restarted. Keep both processes running. Do not expose other services.

In HustleAI > Connect on WhatsApp > Basic setup > Production setup > Configure
Webhooks:

1. Enter `https://<temporary-host>/webhook`.
2. Copy **only** `WHATSAPP_VERIFY_TOKEN` from your local `.env` into Verify token.
3. Verify and save. Backend logs should show `GET /webhook 200`.
4. Subscribe to the `messages` field. Use its dashboard **Test** action. Backend
   logs should show `POST /webhook 200`. Dashboard synthetic senders/other phone
   IDs are ignored; they must never cause messages to unverified recipients.
5. Run `check` to inspect whether the test WABA is subscribed to HustleAI. Callback
   verification alone does not prove WABA subscription. Do not add permissions to
   resolve a failure without approval.

### Publishing blocker checked October 11, 2026

The actual HustleAI configuration dashboard states: unpublished apps receive only
webhooks sent by the dashboard; no production data is delivered, including from
admins, developers or testers. This contradicts older test-number tutorials. A
successful dashboard template send does not establish inbound eligibility.
Inspect Publish's checklist read-only for this app's exact remaining requirements.
The checklist inspected on this date shows a privacy-policy URL requirement and
a disabled Publish button. No publishing settings were changed.
Do not publish, accept terms, add billing, register a real number, or expand access
without explicit confirmation. Until publishing is authorized and requirements
are satisfied, local signed tests and dashboard webhook tests are the available
checks. Do not claim an absent inbound event is a backend failure.

When inbound testing is eligible, send `hello HustleAI` from `+27837758811` to the
Meta test number `+1 555 647 4807`. Expected reply:

> Hi! This is RG Midrand’s HustleAI test assistant. You said: hello HustleAI

## Evidence and limits

```sh
.venv/bin/python -m hustleai.integrations.whatsapp.test_assistant evidence
.venv/bin/python -m unittest tests.unit.test_whatsapp_test_assistant tests.unit.test_gateway -v
```

`queued` proves signed inbound persistence. `accepted` means Graph API returned a
message ID, **not delivery**. Status notifications are recorded without triggering
replies. `delivered`/`read` status evidence must match the outbound message ID in
the private database; aggregate counts alone do not prove a specific reply arrived.
Confirm arrival on the phone as well. `sent` is not confirmed delivery.

Duplicate inbound IDs remain suppressed across restarts. Remote writes are claimed
before sending; a crash or network timeout is marked `sending` or
`uncertain_or_failed` and never resent blindly. Inspect delivery evidence before
manual retry. This conservative test harness has no automated outbound retry.
Unsupported types and other senders are ignored. Replies use the existing
4096-character transport limit, so exceptionally long echoes are truncated.
Health reports HTTP liveness only. The service is a test harness, not a production
throughput or retention implementation.

## Validation completed October 11, 2026

- Full suite: 138 tests run, 36 skipped; all executed tests passed.
- Focused coverage includes webhook verification/signatures, invalid structures,
  persistent duplicates, outbound construction, recipient/type restrictions,
  status-only evidence, prompt acknowledgment and uncertain-send suppression.
- Live HTTP smoke backend with fake outbound transport: public HTTPS health 200,
  verification challenge `42`, signed POST 200 and duplicate POST 200. No Graph
  sends were made. Smoke backend stopped after the test.
- Official cloudflared installed with user approval and temporary tunnel started.
- Actual Meta callback verification/dashboard events remain pending credentials.
- No actual Graph acceptance or phone delivery has been established by these tests.
