# Owner bank-statement reconciliation

Open **Bank reconciliation** in the authenticated RG Midrand dashboard. This
reuses the existing tenant PostgreSQL journal, Zoho Service payment proposals,
owner authentication and WhatsApp outbox. Fictional demo mode is explicitly
labelled and never calls Zoho, SMTP or Meta.

## Upload and review

- PDF: optional local `pdfplumber` extraction, at most 4 MB / 20 pages, isolated
  subprocess with a 45-second timeout. Passwords travel only to the localhost
  backend and parser stdin, are never persisted, and clear after extraction.
  Temporary PDF files have private permissions and are removed after parsing.
- CSV: UTF-8 (including BOM), at most 2 MB / 5,000 rows. Select date format,
  decimal separator and delimiter; custom header mappings are supported.
- PDF tables with Date and Amount/Credit headers produce editable CSV. Review
  the extracted text and each row before analysis. Image/scanned PDFs and layouts
  without a reliable transaction table need a bank CSV export or owner-reviewed
  transcription. Nothing guesses credits from account balances.
- Provide a complete statement start/end period and a stable account label,
  not the account number. Confirm ZAR and verify every amount sign/date/row.
  Only normalized transactions and reports persist; the raw PDF does not.

Typical CSV:

```csv
Date,Name,Reference,Credit,Debit
2026-10-07,Maya Botha,RG-1001,440.00,0
2026-10-08,Unknown payer,Unclear reference,125.00,0
```

Date, reference, description, payer, amount, credit, debit, currency and bank
transaction ID column aliases are supported. Credit/debit columns must have only
one nonzero side per row. Signed Amount columns use positive incoming credits.
No OCR or third-party extraction/model service is used. Actual bank-specific PDF
layout compatibility requires a real, owner-reviewed statement sample.

## Matching and reports

Unique complete invoice references take precedence. Exact client names plus
amount/date can identify a unique invoice for a single customer. Equal names
across different customer IDs, multiple invoice references, duplicate rows,
prepayments, overpayments and invalid data require review. No fuzzy name match
authorizes a payment. The owner may assign an unmatched/review credit to an
invoice after checking the payer, date and current amount constraints.

The report shows credits, suggested/already-recorded matches, excluded/invalid
rows and outstanding invoices due by the declared period end. A suggested bank
credit reduces the *unexplained* amount in the report, never the live ledger.
Current Zoho balances are not historical period-end balances. Other bank accounts,
cash, earlier payments, fees, deposits and incorrect extraction can explain gaps;
flags are potential missing payments, not proof a customer has failed to pay.

Full reports are downloadable Markdown, saved privately in the tenant journal.
At most the first 200 transactions appear on screen; the download includes all.
Re-importing the same content/settings reuses its report snapshot. Use **Refresh current Zoho balances** for a fresh ledger snapshot. This
re-evaluates matches and replaces earlier manual suggestions; previously recorded
receipts remain protected against a second write. Identical uploads otherwise
return their original dated snapshot.

## Recording received funds in Zoho

1. Select **Review Zoho payment** on a unique suggestion.
2. Inspect the invoice, customer, receipt date, amount and deterministic bank
   reference. The backend rereads the live invoice and balance and prepares an
   immutable operation through the existing Service workflow.
3. Verify the money was actually received, then type the exact `CONFIRM <id>`
   into the owner confirmation field. The backend relays that owner-supplied text.
4. Zoho records a bank-transfer customer payment allocated to that invoice.
   Full settlement makes it paid through Zoho's payment workflow; a partial
   receipt only reduces the balance. It does not force a paid flag without funds.

Repeated preparation/confirmation is protected by tenant journal locks, a stable
receipt identity, deterministic bank reference, and existing provider preflight.
Changed customer/currency/status/balance blocks recording. Attempt markers persist
before mutations; provider timeouts or interrupted attempts remain uncertain and
are never blindly retried. Inspect Zoho and the existing operation journal before
manual recovery. Do not invent a new bank transaction ID to bypass duplicate checks.
Without a bank ID, identical receipt evidence cannot prove a second payment;
obtain a better export or reconcile it manually. Split allocations/combined
payments and prepayments need the existing manual payment workflow.

Incomplete imports/provider invoice data block payment recording. No payment
writes occur simply by uploading or matching a statement. No customer reminders
or messages are sent by this feature.

## Owner report delivery

For potentially unexplained overdue balances:

- Dashboard: report is saved immediately and can be downloaded.
- WhatsApp: owner summary is queued only to the verified test recipient, using
  the existing status-tracked outbox. No other recipient is permitted. It waits
  for a real signed inbound event opening Meta's messaging window. Queued/API
  accepted/delivered/read/failed states remain distinct. Unpublished-app inbound
  restrictions therefore still block this channel in the current test setup.
- Email: attach the full Markdown report through your existing SMTP service,
  to one configured owner address. No email provider/account is created.
  Verified TLS on port 465 or STARTTLS on 587 is mandatory. SMTP acceptance is
  not mailbox delivery. Uncertain send outcomes are not automatically retried.

Set up email credentials privately at hidden terminal prompts:

```sh
.venv/bin/python -m hustleai.dashboard.report_setup
```

Restart the live dashboard after setup. A previously unconfigured email delivery
can be attempted once using **Report delivery status** after credentials are set.
Dashboard and WhatsApp notifications are not repeated. Reports are private
financial data: configure only Roland's owner email address. Do not paste SMTP
passwords or statement contents in chat.

The PDF extra is declared as `.[bank]` in pyproject.toml and was installed with
owner approval on 15 October 2026 using audited pinned dependencies. Real local
PDF table extraction passed with a fictional fixture. No bank-specific statement
or real receipt has been processed yet; the actual bank layout still needs review.

## Checks

```sh
.venv/bin/python -m unittest tests.unit.test_bank_reconciliation tests.unit.test_bank_pdf_email -v
node --check src/hustleai/dashboard/static/bank.js
```

Coverage includes names/references/date/amount, duplicate names and rows,
partial/unmatched balances, locale parsing, currency validation, current-customer
and balance rechecks, exact owner confirmation, repeated clicks/imports, uncertain
provider writes, local PDF table/password/timeout handling and owner-only TLS email.

The initial validation ran 188 tests (152 passed, 36 skipped), with PDF/SMTP
provider calls mocked. The security update also added a real PDF extraction test
and installed the parser; see the dependency security guide for the current suite
result. SMTP remains unconfigured and has not been tested with a real mailbox. Live read-only Zoho checks returned 656 invoice
and 270 payment summaries; all invoices contained required reconciliation fields.
Browser demo checks exercised matching, the missing-payment report and exact
owner payment confirmation. No live Zoho payment or email was submitted.
