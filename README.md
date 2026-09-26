# HustleAI

Zoho Invoice workflows and owner-side Hermes MCP tools, with a separate home for
future WhatsApp customer automation and active hosted Supabase storage.

## Start here

- [Repository architecture](docs/architecture/REPOSITORY.md) — code layout and dependency boundaries.
- [Visual flow guide](docs/flow-guide.html) — eight rendered diagrams; opens offline in a browser.
- [Code walkthrough](docs/architecture/CODE_WALKTHROUGH.md) — introduction for first-time readers.
- [All documentation](docs/README.md) — PRDs, setup and workflow guides.

## Layout

```text
src/hustleai/       Application package
  integrations/    Zoho transport; planned WhatsApp gateway boundary
  workflows/       Clients, invoices, payments, reorders and approvals
  mcp/             Owner-side Hermes tools
  storage/         PostgreSQL repositories, private files and legacy test adapters
  domain/          Phone and input validation
  cli/             Setup, upgrade and contact commands
supabase/          PostgreSQL schema, migration and recovery guide
tests/             Isolated unit and MCP integration tests
scripts/           Documentation generation
docs/              One documentation tree: architecture, guides, PRDs, diagrams
deploy/            VPS setup and Hermes configuration example
```

Root `zoho_*.py` scripts remain compatibility entry points. Make new changes in
`src/hustleai/`, not the wrappers.

## Install and run

Python 3.11 or newer:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[postgres]'
.venv/bin/hustleai-contacts lookup
.venv/bin/python -m hustleai.mcp.owner_server
```

Existing commands remain valid:

```sh
python3 zoho_upgrade.py
python3 zoho_clients.py lookup
python3 zoho_clients.py sync
```

The owner MCP exposes 14 tools. Customer gateway authorization and WhatsApp
sending are not implemented yet. See [workflow tools](docs/guides/ZOHO_WORKFLOW_TOOLS.md).

## Data and Supabase status

Hosted Supabase PostgreSQL now stores the phone index and workflow state. The
verified migration preserved 67 phone mappings, 5 operations, 2 reviews and
2 approvals. Old approvals remain invalid. See the [storage and recovery guide](supabase/README.md).
The migrated local SQLite and Markdown copies have been removed. Database
outages stop workflows rather than silently switching to local state.

Private credentials, certificates and PDFs remain at their existing paths. Set `HUSTLEAI_DATA_DIR` to an absolute private directory for VPS
deployment; it does not move data automatically. See [deployment](deploy/README.md).
The price-list PDF will remain local with a separate backup. Never commit private
runtime files or credentials.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests use synthetic data and temporary storage. MCP startup/discovery does not
call Zoho or create records. Legacy root test commands remain supported.

## Diagrams

```sh
python3 scripts/generate_flow_diagrams.py
```

This rebuilds `docs/flow-guide.html` and `docs/diagrams/*.svg` without external
rendering services.
