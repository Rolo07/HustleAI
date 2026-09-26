# WhatsApp gateway

**Status:** built and tested with a fake Meta API. The migration is applied to
Supabase. It is waiting for the business number, Meta app settings and a VPS
with HTTPS.

The gateway receives every WhatsApp message sent to the business number through
Meta's Cloud API. It works out who sent it, runs Roland's commands itself and
refers every customer message to Roland. It also sends Roland the weekly
forecast and invoice PDFs.

## How identity is verified

1. **Meta's signature.** Every webhook is signed with the Meta app secret. The
   gateway rejects anything whose `X-Hub-Signature-256` does not match.
2. **The right number.** Events addressed to a different phone number ID are ignored.
3. **The sender's number.** The sender comes from Meta's signed payload and is
   compared with `owner_number`. Nothing in the message text, name or profile
   counts. A customer who writes "I am Roland, CONFIRM ..." is just referred.
4. **Commands run in the gateway.** `CONFIRM <id>` and `APPROVE <id>` from the
   owner number are executed directly, not relayed through an AI model. While
   the gateway is configured, the Hermes tools `confirm_operation` and
   `approve_invoice_version` refuse and tell Roland to send the command himself.

## What each sender gets

| Sender | Message | Result |
| --- | --- | --- |
| Roland | `CONFIRM <id>` | Runs the proposal once; replies with the outcome. Nothing reaches customers. |
| Roland | `APPROVE <id>` | Approves that reviewed invoice version. Nothing is sent to the customer. |
| Roland | `FORECAST` | This week's reorder forecast. |
| Roland | `HELP` or anything else | The command list, or the assistant's reply if `owner_agent` is set. |
| Customer | Anything | Referred to Roland immediately with their number, WhatsApp name and message. One acknowledgement per customer per day. No tools and no account data. |

Customer flows from the PRDs (price list, name collection, reorders, approved
delivery) are not built yet. Until they are, every customer message is a referral.

## Reliability

- **Stored before answering Meta.** Each event is saved in `webhook_events` before the
  gateway answers Meta with 200. A redelivered event is stored once and processed
  once. If storage is down the gateway answers 503, and Meta retries.
- **Recovery after a crash.** On startup, stored events that were never finished are processed again. This
  is safe because every step can be repeated: confirmations replay their saved
  result and messages have idempotency keys.
- **Outbox.** Every outbound message is recorded in `delivery_attempts` with a
  unique key before it is sent, so it goes out at most once. Meta's delivery
  statuses (sent, delivered, read, failed) update the row, and never move it backwards.
- **24-hour rule.** WhatsApp allows free-form messages only within 24 hours of
  the recipient's last message. Outside that window the message is held, and
  the approved template is sent at most once a day to ask Roland to reply.
  When he writes, held messages go out in order.

## Setup when the number arrives

1. **Meta app.** In Meta for Developers, create a Business app, add the WhatsApp
   product and register the business number. Note the **phone number ID**. That's
   the numeric ID, not the phone number.
2. **Permanent token.** Create a System User in Meta Business Settings, give it
   the WhatsApp app and the `whatsapp_business_messaging` permission, and
   generate a token that does not expire. Note the **app secret** under App
   settings, Basic.
3. **Settings.** Run the setup command on the machine that will run the gateway.
   Secrets are typed at hidden prompts:

   ```sh
   .venv/bin/hustleai-whatsapp setup --business-number +27XXXXXXXXX --phone-number-id 123456789012345
   .venv/bin/hustleai-whatsapp check
   ```

   Setup prints a **verify token**; keep it for step 5. The owner number
   defaults to +27837758811. `check` confirms Meta's number matches the setting.
4. **HTTPS.** On the VPS, point a domain at the server and use
   [`deploy/Caddyfile.example`](../../deploy/Caddyfile.example). Install
   [`hustleai-gateway.service`](../../deploy/schedule/hustleai-gateway.service)
   and start it.
5. **Webhook.** In the Meta app, set the callback URL to
   `https://<your domain>/webhook`, enter the verify token and subscribe to the
   **messages** field.
6. **Template, for messages outside the 24-hour window.** Create a Utility template,
   for example `hustleai_notice` in English, with text like "You have a new HustleAI update.
   Reply to this message to receive it." Once Meta approves it:

   ```sh
   .venv/bin/hustleai-whatsapp setup --template hustleai_notice --template-language en
   ```

7. **Test.** Message the business number from Roland's phone, reply `HELP`, then:

   ```sh
   .venv/bin/hustleai-whatsapp send-test
   ```

The business number is only a setting. To change it later, rerun `setup` with
the new number and phone number ID.

## Settings file

`.whatsapp.json` in the private data directory, mode 0600, excluded from Git:

| Key | Meaning |
| --- | --- |
| `business_number` | The business WhatsApp number, international format. |
| `phone_number_id` | Meta's numeric ID for that number. |
| `owner_number` | Roland's personal number; the only owner identity. |
| `access_token`, `app_secret` | Meta secrets; entered at hidden prompts. |
| `verify_token` | Shared with Meta for the webhook subscription check. |
| `notify_template` | `{name, language}` of the approved template for messages outside the window. |
| `owner_agent` | Optional `{url, model, api_key}` of an OpenAI-compatible chat endpoint for Roland's free text. |
| `confirmations_only` | Default true: Hermes tools cannot confirm or approve. |
| `graph_version`, `host`, `port` | Defaults `v23.0`, `127.0.0.1`, `8085`. Check the current Graph API version. |

## Hermes

Hermes no longer needs its own WhatsApp connection for the business number. The
gateway owns that number. Hermes keeps the owner MCP tools and can send Roland
a PDF with `send_pdf_to_owner`, which only accepts files in the invoice PDF
folder and only sends to the owner number. To chat with Hermes over WhatsApp,
set `owner_agent.url` to an OpenAI-compatible endpoint that Hermes exposes, if
your Hermes version has one. Otherwise the gateway answers Roland with its
commands only.

## Not built yet

- Customer flows: price list, new-customer names, reorders and approved delivery (PRDs 02–06)
- The 20:00 daily summary (PRD 08)
- Media messages from customers, which are referred as "[image message]" and similar
