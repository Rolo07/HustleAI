# Multi-tenant HustleAI on Hermes

One installation serves several businesses, called tenants. **RG Midrand is
tenant 1** (`rg-midrand`). Each tenant has its own settings, secrets, database
login, WhatsApp number and Hermes profile. No tenant can see another's data.

## How a tenant is put together

```text
/var/lib/hustleai/tenants/<slug>/      private folder, mode 0700
  tenant.json              business settings (no secrets)
  .zoho-credentials.json   Zoho sign-in for this business
  .supabase-runtime.json   this tenant's own database login
  .whatsapp.json           this tenant's business number and Meta secrets
  invoice-pdfs/ reports/   PDFs, forecasts and reports/jobs.log
~/.hermes/profiles/<slug>/            Hermes profile for this tenant
  SOUL.md  config.yaml  .env  scripts/  cron jobs
```

`tenant.json` settings:

| Key | Default | Meaning |
| --- | --- | --- |
| `name`, `owner_name`, `owner_number` | — | Business name, owner's first name used in messages, owner's WhatsApp number |
| `organization_id` | — | Zoho organization ID |
| `currency` | `ZAR` | Invoice currency; clients and invoices in other currencies are rejected |
| `timezone` | `Africa/Johannesburg` | Used for "today", forecasts and daily limits |
| `payment_terms_days` | `7` | Days until an invoice is due |
| `vat_registered` | unset | `false` means invoices never show tax |
| `country_code` | `27` | For local phone numbers starting with 0 |
| `features` | all | Any of `invoicing`, `forecast`, `orders_sync`, `whatsapp`; tools and jobs for disabled features are not offered |
| `forecast_start_date`, `test_invoice_ids` | unset | Forecast settings |

A single-business install still works without `tenant.json`: the old
`.zoho-local.json` is read, and `tenant.json` overrides it where both are set.

## Isolation

- **Database.** Every tenant logs in with its own restricted role,
  `hustleai_rt_<slug>`, mapped to one organization in `tenant_roles`. One policy
  per table admits only rows for that organization. A bug in the app still
  cannot read or write another tenant's rows. Tests prove this with two tenant
  roles.
- **Files.** Each tenant folder must be a real directory owned by the service
  user with mode 0700. Otherwise it is skipped.
- **WhatsApp.** The gateway serves `/webhook/<slug>`. Each route checks that
  tenant's own Meta app secret and phone number ID, so one tenant's signed
  message is rejected at another tenant's address.
- **Hermes.** Each profile's MCP server is bound to its own tenant folder. The
  terminal, file, code-execution, browser and delegation toolsets are turned
  off, because all profiles run as one operating-system user.

## Adding a tenant

On the VPS, as the service user:

```sh
hustleai-tenant create shop-one --name "Shop One" --owner-name Thandi \
  --owner-number +27821234567 --organization-id 123456789 --vat-registered no
HUSTLEAI_DATA_DIR=/var/lib/hustleai/tenants/shop-one hustleai-zoho-setup
hustleai-whatsapp setup --tenant shop-one --business-number +27... --phone-number-id ...
hustleai-whatsapp check --tenant shop-one
hustleai-tenant hermes-install shop-one          # add --dry-run to preview
```

`create` needs the administrative Supabase settings to create the database
login. They stay on the maintenance machine or in the default data folder,
never in a tenant folder. In the tenant's Meta app, set the webhook to
`https://<domain>/webhook/shop-one`.

## Moving RG Midrand to tenant 1

The current single-business install already is RG Midrand. The move copies
files and doesn't touch the database, because the organization ID stays the same.

```sh
hustleai-tenant import-legacy rg-midrand --from /var/lib/hustleai \
  --name "RG Midrand" --owner-name Roland --owner-number +27837758811
hustleai-tenant hermes-install rg-midrand
```

`import-legacy` copies the Zoho credentials, database login, WhatsApp settings,
PDFs and reports. It writes `tenant.json`, leaves the source folder unchanged,
and confirms the orders are visible through the new folder. A trial run on
2026-09-29 showed all 654 orders.

## What `hermes-install` does

Every step can be run again safely.

1. Creates the Hermes profile if it doesn't exist, and writes `SOUL.md` naming the owner and business.
2. Adds the `hustleai` MCP server with `HUSTLEAI_DATA_DIR` set to the tenant
   folder, and disables the terminal, file, code-execution, browser and delegation toolsets.
3. Enables the API server with its own `API_SERVER_KEY`, and sets
   `HERMES_TIMEZONE` to the tenant's timezone. If WhatsApp is set up, it points
   the gateway's owner assistant at `http://127.0.0.1:8642/p/<slug>/v1/chat/completions`.
4. Writes script-only cron jobs for the orders sync and the forecast. See
   [SCHEDULED_TASKS.md](../../SCHEDULED_TASKS.md).
5. Enables the `hustleai` plugin, which adds the `hustleai-owner` skill, and
   turns on `gateway.multiplex_profiles` so one Hermes process serves every tenant.

Hermes needs the package installed with its extra:
`pip install -e '.[postgres,hermes]'`. The gateway, not Hermes, owns each
business number. Hermes's own WhatsApp adapter only answers allowlisted
senders and does not handle the 24-hour rule, so customers would get no
answer and no referral.

## After installing on the VPS

- `hermes plugins doctor` and `hermes -p <slug> mcp test hustleai`
- `curl -H "Authorization: Bearer <key>" http://127.0.0.1:8642/p/<slug>/v1/toolsets`
  should list no terminal or file tools
- `hermes -p <slug> cron list` should show the jobs at the expected local
  times. Confirm Hermes applies `HERMES_TIMEZONE` to cron, or set the server
  timezone to match.
- `hustleai-tenant list` should show every tenant with its features and WhatsApp status

## Not built yet

- A container per tenant for stronger separation between Hermes profiles
- Zoho onboarding through a web sign-in instead of the Self Client grant code
- Meta Tech Provider status and Embedded Signup for clients' own numbers
- Billing by feature, POPIA agreements, per-tenant export and deletion,
  per-tenant backups and monitoring
