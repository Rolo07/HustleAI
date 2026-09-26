# Storage boundary

- `backend.py`: explicitly selects `postgres` or the legacy `sqlite` backend;
  blocks service initialization during maintenance. No outage fallback.
- `repository.py`: organization-scoped operation and invoice review methods.
- `postgres/repository.py`: verified TLS sessions, transactions, row claims,
  advisory locks and phone mappings.
- `postgres/upgrade.py`: checksum-verified additive SQL upgrades; no data import.
- `postgres/migrate.py`: private snapshots, tracked schema import, restricted
  runtime role, data reconciliation and configuration cutover.
- `legacy/`: retained SQLite/Markdown backend for tests and migration history.
- `files.py`: owner-only, atomic, fsynced local file replacement.

Workflows contain no SQL. Every MCP service call closes its connection. Runtime
connections use `.supabase-runtime.json`; administrative credentials are separate.
See the repository's [Supabase guide](../../../supabase/README.md) for operating
instructions, the completed migration and recovery limitations.

The PostgreSQL repository delegates claims, successful results, review revocation
and approval persistence to four stored functions. Simple reads remain bound
queries. See [function documentation](../../../docs/guides/DATABASE_FUNCTIONS.md).
