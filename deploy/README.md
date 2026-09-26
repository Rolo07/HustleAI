# VPS deployment layout

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

Follow the [storage and recovery guide](../supabase/README.md). Configure separate
encrypted off-device backups for runtime secrets, the price-list PDF and other
required files; hosted PostgreSQL does not store those files. Scheduled backup
and recovery automation are not yet deployed.
