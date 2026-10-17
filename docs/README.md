# Documentation

- [Owner dashboard](guides/OWNER_DASHBOARD.md): local preview, owner authentication, reorder versions, takeover and live integration setup.

- [Repository structure](architecture/REPOSITORY.md): where code belongs and what is active versus planned.
- [Code walkthrough](architecture/CODE_WALKTHROUGH.md): first-time introduction with diagrams.
- [Visual flow guide](flow-guide.html): nine rendered flows, browser navigation and zoom.
- [Product requirements](prds/README.md): each customer and owner workflow.
- [Zoho workflow tools](guides/ZOHO_WORKFLOW_TOOLS.md): reorder retrieval, draft updates and approvals.
- [Weekly reorder forecast](guides/REORDER_FORECAST.md): customers expected to order in 7–14 days, stock totals and delivery areas.
- [Multi-tenant on Hermes](guides/MULTI_TENANT.md): tenants, isolation, adding a business, RG Midrand as tenant 1.
- [WhatsApp gateway](guides/WHATSAPP_GATEWAY.md): identity checks, owner commands, setup steps for the business number.
- [Scheduled tasks](../SCHEDULED_TASKS.md): every scheduled job with its days and times.
- [Hermes setup](guides/HERMES_SETUP.md): permissions and operating instructions.
- [Supabase migration](../supabase/README.md): active hosted storage, verified migration and recovery.
- [VPS deployment](../deploy/README.md): package installation and private-data configuration.
- [Isolated VPS dashboard](guides/VPS_DASHBOARD.md): private access, exact HTTPS webhook routes, restartable services, encrypted backups and recovery.
- [Stable VPS HTTPS](guides/STABLE_HTTPS.md): trusted HTTPS on the existing public IP, automatic renewal and Meta callback cutover without buying a domain.

- [Database functions and queries](guides/DATABASE_FUNCTIONS.md): atomic workflow transitions, permissions and upgrades.

- [Bank-statement reconciliation](guides/BANK_RECONCILIATION.md) — upload, match, review Zoho payments and owner reports.

- [Dependency security and audit](guides/DEPENDENCY_SECURITY.md) — pinned packages, runtime policy and automatic checks.
