# VPS deployment

## Quick install

On the server, as root:

```sh
curl -fsSLO https://raw.githubusercontent.com/Rolo07/HustleAI/main/deploy/install.sh
sudo bash install.sh
```

No other computer is needed. The installer asks for every ID, key and secret.
Have these ready; a phone browser is enough:

- **Zoho:** the organization ID, plus the Client ID and Client Secret of a
  Self Client at https://api-console.zoho.com. The installer shows the scopes
  to paste when you generate its 10-minute code.
- **Supabase:** Project, then Connect, then Session pooler. You need the
  connection string and the database password. The installer creates this
  server's own restricted login from them, and can move RG Midrand away from an
  old login, such as the Mac's. Afterwards it asks whether to keep the admin login.
- **The AI model** Hermes should use, and its API key.
- **Meta WhatsApp:** the phone number ID, permanent access token and app secret.
- **A domain** whose DNS points at the server.

It sets the timezone and firewall, creates the `hermes` user, installs the
code, sets up RG Midrand as tenant `rg-midrand`, installs Hermes with its
profile and scheduled jobs, and starts the WhatsApp gateway behind HTTPS. Run
it again at any time to update or change a setting: finished steps are skipped.

**Hermes already installed?** The installer finds the user that owns
`~/.hermes` and runs HustleAI as that user, reusing the same Hermes. It
doesn't reinstall Hermes or ask for the AI model again unless you choose to.
Don't connect RG Midrand's business number to Hermes's own WhatsApp
adapter, because the HustleAI gateway must own that number. The installer also asks
before turning on the firewall or changing the timezone. If another web
server already uses ports 80/443, it leaves that server alone and tells you
what to forward.

The manual steps below explain what it does.

## Manual layout

Install source separately from private state:

```sh
cd /opt/hustleai
python3 -m venv .venv
.venv/bin/pip install -e '.[postgres]'
```

Use `HUSTLEAI_DATA_DIR=/var/lib/hustleai` (or another absolute directory owned by
the Hermes service user). Create it privately and migrate the existing private
files as a separate, verified operation. Setting this variable alone does not
copy any data. Paths stored inside existing review records must be reconciled
when PDFs move; do not silently approve using missing or relocated files.

See `hermes-config.example.yaml` for the owner MCP entry. Secrets belong in
private runtime files or a secret manager, never in this example or prompts.
The business WhatsApp gateway and scheduled jobs are not deployed by this file.

Storage is now hosted Supabase. Preserve `storage_backend: postgres` in
`.zoho-local.json`. Copy `.supabase-runtime.json`, `.zoho-local.json`,
`.zoho-credentials.json`, the CA certificate and required PDFs privately into the
data directory. Set file permissions to `0600` and directory permissions to
`0700`. Update `sslrootcert` in the runtime settings to the certificate's VPS
path. Keep administrative Supabase credentials off the runtime VPS unless a
specific maintenance task requires them.

The migrated local SQLite and Markdown files have been removed. Do not rerun
the initial import on the VPS. Both installations would connect to the same hosted records;
stop the Mac workflow processes before enabling the VPS instance. Historical
reviews were invalidated at cutover; generate fresh previews after deployment.
Database migrations never send Zoho invoices or WhatsApp messages.

**WhatsApp gateway.** Follow [the gateway guide](../docs/guides/WHATSAPP_GATEWAY.md):
run `hustleai-whatsapp setup`, put HTTPS in front with `Caddyfile.example`, and
enable `deploy/schedule/hustleai-gateway.service`. All schedules and times are
in [SCHEDULED_TASKS.md](../SCHEDULED_TASKS.md).

**Weekly forecast go-live.** On the day the VPS starts serving, run
`hustleai-forecast start-date today` so the reorder forecast only learns from
orders placed from then on. Then enable `deploy/schedule/hustleai-orders-sync.timer` (nightly 22:00) and
`deploy/schedule/hustleai-forecast.timer`
(see [the forecast guide](../docs/guides/REORDER_FORECAST.md)). Do not also run
the Mac schedules; unload both Mac launch agents first.

Follow the [storage and recovery guide](../supabase/README.md). Configure separate
encrypted off-device backups for runtime secrets, the price-list PDF and other
required files; hosted PostgreSQL does not store those files. Scheduled backup
and recovery automation are not yet deployed.
