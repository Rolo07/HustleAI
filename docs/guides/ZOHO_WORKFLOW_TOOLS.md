# Reorder retrieval, draft revisions and version-specific approval

Implementation status: available locally as owner-side MCP tools. Automated
transport/schema and workflow tests passed. Live draft PUT and version-specific
approval tests passed on 2026-09-26 using the upgraded UPDATE grant. The initial implementation created no live records; the later authorized live
test created the draft recorded below.
Customer-scoped gateway authorization, WhatsApp sending and sent-state updates
are not implemented here.

## Tools

| Tool | Inputs and outcome |
| --- | --- |
| `retrieve_reorder` | Cellphone, optional contact ID and source invoice ID. Returns prior invoice, fingerprint, current product data and review blockers. |
| `update_draft_invoice` | Cellphone, draft ID, complete desired line list, expected version and optional contact ID. Returns a confirmation proposal, not an immediate update. |
| `review_invoice` | Cellphone, draft ID and optional contact ID. Saves a private versioned PDF and returns review ID, fingerprint and approval phrase. |
| `approve_invoice_version` | Review ID and Roland's exact `APPROVE <review_id>` response. Saves local approval bound to content, recipient and PDF. |
| `validate_invoice_approval` | Approval ID. Rechecks the current invoice, recipient lookup and PDF hash. Does not send or mark sent. |

## Reorder selection

The default selects the greatest invoice date among issued ZAR invoices with
status sent, overdue, paid, partially_paid or unpaid. Drafts and void invoices
are excluded. Known test invoices are excluded using `test_invoice_ids` in local
configuration and explicit TEST markers in invoice references, notes, numbers or
line descriptions. Add test IDs when fixtures do not use those markers. A date
tie requires an explicit source invoice selection; it never guesses a winner.

An empty or ambiguous history raises a clear error for referral. Current catalog
records are returned alongside historical lines. Catalog prices are not assumed
VAT-inclusive; tax, inactive items, delivery charges and discounts need review.
No customer confirmation, new draft creation or automatic repricing is performed
by retrieval itself. Production selection rules should be reviewed with Roland
before enabling the customer reorder workflow.

## Revision and approval flow

```mermaid
sequenceDiagram
    participant R as Roland
    participant M as Owner MCP
    participant J as Local journal
    participant Z as Zoho
    M->>Z: Read draft and download PDF
    M->>Z: Re-read invoice to detect changes
    M->>J: Save review, full-content fingerprint and PDF hash
    M-->>R: Versioned preview and review ID
    alt Changes requested
        R->>M: Full desired lines for reviewed version
        M-->>R: Before/after update proposal
        R->>M: CONFIRM operation_id
        M->>Z: Recheck live draft and expected fingerprint
        M->>J: Revoke previous reviews and approvals
        M->>Z: PUT replacement lines to same invoice
        M-->>R: Update result; generate fresh review PDF
    else Approve reviewed version
        R->>M: APPROVE review_id
        M->>Z: Recheck live contents and client association
        M->>J: Save version-specific approval
        M-->>R: Approval ID; invoice remains unsent
    end
```

`update_draft_invoice` takes the **complete desired line list**. Omitted lines
are removed; it is not a partial patch. Lines use the same schema as
`create_invoice`: explicit inclusive rate, positive quantity, item_id or
description, and configured tax_id or explicitly approved no_tax. Existing
invoice date and due date are preserved. Header charges and discounts are not
changed, so the displayed line subtotal is not necessarily the final total.
Drafts with line discounts are rejected until discount-preserving edits are
supported. Sent, paid and otherwise non-draft invoices cannot be revised here.

A proposal records the expected current version. It must pass that check both
when prepared and immediately before PUT. The existing `confirm_operation`
mechanism provides single-attempt execution for an operation ID. An uncertain
PUT remains attempted and is not retried. Prior approvals are revoked before
PUT even if the update fails. Call `review_invoice` after reconciling the result
to obtain a fresh preview and approval.

## Approval semantics

- Invoice JSON is canonically hashed with SHA-256, excluding the transient
  `invoice_url` portal link (which changes between otherwise identical reads).
  Key order does not matter;
  content and metadata changes do. Conservative metadata invalidation may require
  another review even when the visible invoice looks unchanged.
- Review generation checks the invoice before and after downloading the PDF.
- Each PDF gets a unique local path; subsequent previews do not overwrite it.
- Approval is bound to organization, invoice, selected client, normalized phone,
  invoice fingerprint and saved PDF hash.
- A new review supersedes previous reviews/approvals for that invoice.
- Reviews and their approval validity expire 30 minutes after review creation.
- Repeating a valid approval returns the same ID rather than duplicating it.
- Owner preview, approval and validation never send messages or mark invoices sent.
- A gateway must authenticate Roland. Model-supplied text is not independent
  proof of owner identity. Keep these tools out of public customer sessions.

Per-invoice file locks serialize workflow actions within this local installation.
They cannot lock Zoho's UI or other integrations. Preflight checks reduce stale
updates but cannot provide a remote atomic compare-and-swap guarantee. Likewise,
`validate_invoice_approval` is a point-in-time check, not a one-time send claim.
The future delivery adapter still needs atomic dispatch state, provider message
IDs, deduplication, final checks and delivery receipt handling.

## Permissions and setup

`zoho_upgrade.py` now requests `ZohoInvoice.invoices.UPDATE` in addition to the
previous scopes. Existing refresh tokens do not gain it automatically. Run:

```sh
python3 zoho_upgrade.py
```

Use the scope line printed by the script in the same Self Client, then paste a
fresh grant code into its hidden terminal prompt. Do not share the code in chat.
Reorder reads require contacts.READ, invoices.READ and settings.READ; review PDFs
and approvals do not require UPDATE because they do not modify Zoho.

Install the complete package with its `postgres` extra when deploying. Supabase
now stores the journal, reviews and approvals. PostgreSQL advisory locks
coordinate invoice workflows across app processes. Review PDFs remain local and
private. See the [storage and recovery guide](../../supabase/README.md).

## Validation

```sh
.venv/bin/python -m unittest -v test_zoho_tools.py test_zoho_workflows.py
```

Tests cover history exclusions, latest-date ties, current product reads, same-ID
updates, preserved dates, exact confirmations, repeat calls, failed writes,
stale preconditions, sent/paid rejection, concurrent external edit detection,
PDF tampering, expiry, superseded approvals and wrong-client requests. They use
isolated temporary configuration and no real credentials or Zoho writes.

API reference: [Zoho invoice endpoints](https://www.zoho.com/invoice/api/v3/invoices/).

## Live test — 2026-09-26

Using Roland's authorized test client, created draft INV-000656 (ID
3314726000000848008) with one Bushveld Harvest at R111, then updated the same
invoice to quantity two, R222 total. Due date remained 2026-10-03. Used no tax
for this explicit test, consistent with the previously approved test setup.

Verified owner PDF preview, initial approval, confirmed PUT, repeat-confirmation
replay, rejection of the earlier approval, fresh PDF/version and new approval
validation. Invoice stayed draft and is_emailed=False. No payment was recorded
and no customer message was sent. Test approvals were explicitly revoked after
validation. The test draft remains for Roland to inspect/delete.

The live test exposed a changing invoice_url portal link on otherwise identical
GET responses. Version hashing now excludes that one transient field; all other
fields remain protected. A regression test proves URL rotation does not reject
a review, while an actual notes edit still invalidates approval.

Reorder history excludes Roland's test invoices. Positive selection, date ties
and current-product lookup are covered with isolated fixture tests because
Roland has no eligible real order to use for a positive live reorder test.
