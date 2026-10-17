# RG Midrand WhatsApp release test plan

Prepared: 18 October 2026 · Release decision: **NOT READY — validation pending**

Purpose: prove the deployed customer-to-invoice workflow before replacing the
Meta test number with the real RG number. This is a test specification, not a
claim that the scenarios have passed. Every scenario below starts **NOT RUN**.
Earlier unit-test passes, screenshots and individual API checks are supporting
evidence only; they do not count as acceptance of this release.

## Scope and boundaries

- Test the exact deployed browser UI, Python backend, Hermes, Zoho, database,
  WhatsApp webhooks and actual recipient delivery together.
- Continue using the existing Meta test account and approved test recipient.
  Use clearly marked test invoices and fixtures. No real customer messages,
  payments, billing changes, WhatsApp account deletion/migration, app publishing
  or real-number registration are authorized by this document.
- Use an isolated database and test fixtures for destructive, concurrency,
  cross-customer and dependency-failure scenarios. Do not stop or corrupt the
  shared Hermes gateway, original public Caddy, live Zoho data or production DB.
- Test with the owner operating the real dashboard. A successful direct API call
  cannot replace a browser acceptance test. Confirm outcomes independently in
  Zoho and the recipient's WhatsApp, not only in dashboard state.
- Current deployment deliberately pins a Meta test phone/account and recipient.
  Real-number support needs a separately reviewed configuration/code cutover.
  A new token or phone number alone is not a production migration plan.
- Catalogue aliases, test-history exception, physical-stock confirmation,
  paid-delivery carry-forward and approval rules are all part of scope.
- If bank reconciliation, forecasting, email or other optional features remain
  enabled at launch, their applicable scenarios are release gates too. Otherwise
  record that they are unavailable to users and why; do not silently omit them.

## Execution and evidence

**Roles:** Codex/technical tester prepares fixtures, instruments failures and
records evidence. Roland performs owner acceptance, confirms business facts and
makes the final release decision. No test outcome can substitute for his approval.

**Priorities:** P0 = privacy, authorization, wrong recipient, duplicate/incorrect
invoice or financial corruption. P1 = broken essential workflow, incorrect product,
stock, delivery, recovery or unusable owner control. P2 = polish/usability that
does not conceal a failure or change an outcome. P0/P1 failures block release.

For each row, record PASS / FAIL / BLOCKED / NOT APPLICABLE in the execution log.
NOT APPLICABLE needs a concrete reason and owner approval. BLOCKED is not PASS.

Required evidence: scenario ID, time in Africa/Johannesburg, application commit
and installed-package version/hash, configuration mode (no secrets), fixture IDs,
steps, actual result, screenshot of the relevant UI, sanitized logs, message ID
and recipient-side delivery where applicable, Zoho invoice ID/version/total,
defect link and tester. Record expected absence of side effects as well as success.
Keep customer data, invoices, screenshots containing personal data, tokens and
raw logs in private `var/acceptance/<run-id>/`, not in GitHub. Commit only sanitized
results and defect descriptions. Use the run-log template alongside this plan.

**A PASS requires all layers agree:** UI result + durable state + provider result.
For example, a send returning HTTP 200 is not proof of WhatsApp delivery; a
locally calculated total is not proof the Zoho PDF has that total.

## Test fixtures and prerequisites

Create a fixture register before execution; use owned/test-only records:

| Fixture | Required data |
|---|---|
| F1 Known customer | Unique phone match; issued paid test invoice; identifiable product and quantity |
| F2 Delivery as shipping | Paid invoice with a distinct shipping amount, e.g. R40 |
| F3 Delivery as line | Paid invoice with an active Delivery/Delivery 2 line; different current catalogue rate |
| F4 No delivery / unpaid | No fee; separately unpaid/part-paid invoice with delivery |
| F5 Stock variants | Adequate, insufficient, zero, unknown and inactive product states in isolation |
| F6 Identity/history | Unknown phone, duplicate phone match, no orders, same-date orders, wrong-customer invoice |
| F7 Complex order | Two products, decimal quantities, discount, tax ambiguity and changed catalogue price |
| F8 Isolation | Second synthetic customer and separate tenant/store; never reuse real personal records |
| F9 Recovery | Encrypted backup and disposable restore database/file tree; no production restore |

Freeze the candidate commit, compare VPS source **and imported site-packages**,
record dependency lock, confirm backup freshness and define a rollback target.
Verify the current access token without sending. Record its expiry privately.
Identify which tests can run in the current allowlisted test deployment and which
need the isolated environment. Do not weaken live recipient restrictions to test.

## 1. Browser access and owner controls

| ID | Priority | Scenario / action | Required result |
|---|---|---|---|
| UI-01 | P1 | Open HTTPS dashboard in a fresh supported browser | Trusted connection; login loads; no blank page or console-blocking error |
| UI-02 | P0 | Visit protected routes while logged out; wrong password | No customer/financial data; clear login failure; no authenticated session |
| UI-03 | P0 | Correct login, logout, expired session, backend restart | Auth works; logout denies access; restart requests sign-in clearly without losing saved work |
| UI-04 | P1 | Click Prepare reorder with valid history | Visible progress, then order details in the same panel; no silent/no-op result |
| UI-05 | P1 | Preparation fails: history, catalogue or provider unavailable | Useful error beside the order and notification; retry possible; no false success |
| UI-06 | P1 | Edit quantity/product; add/remove a product; cancel edit | Totals update on save; cancelled edit changes nothing; errors remain visible in modal |
| UI-07 | P0 | Edit/reject/approve a reply; double-click approval | Exact reviewed text only; rejected text stays unsent; one queued message at most |
| UI-08 | P0 | Take over while reply is generating/queued; then resume | No new automated send after takeover returns; resume is explicit; private notes never sent |
| UI-09 | P1 | Refresh, reconnect, switch conversations during actions | Correct customer remains selected/bound; saved state persists; no cross-conversation action |
| UI-10 | P1 | Slow network, error response, session expiry mid-action | Busy state and actionable result; no duplicate action or lost error after polling |
| UI-11 | P2 | Laptop, narrow/mobile viewport, keyboard-only controls | Order/error/approval controls readable and reachable; no overlapping or clipped controls |

## 2. Conversation and product understanding

| ID | Priority | Scenario / action | Required result |
|---|---|---|---|
| CHAT-01 | P1 | Greeting, short enquiry and follow-up | Brief, friendly, natural RG Midrand tone; no repeated formal opening |
| CHAT-02 | P1 | “I want 10 kg chicken meal” | Resolves owner-approved alias to Rich in free range chicken; does not invent feed/prepared meals |
| CHAT-03 | P1 | “Another 10kgs”, “same again”, “repeat my order” | Looks up this customer's eligible history; proposes correct product and newly requested quantity |
| CHAT-04 | P1 | Previous order differs from current explicit choice | Current choice wins; unrelated historic products are not re-added |
| CHAT-05 | P1 | Say “Yes” after a delivered product/quantity question | Acknowledges that selection once; no repeated product question |
| CHAT-06 | P1 | Say “No”, correct product, correct quantity, then confirm | Uses corrected selection; does not return to the rejected choice |
| CHAT-07 | P0 | Say “Yes” after an unsent, failed or private-note question | Does not treat hidden/failed text as customer-visible confirmation |
| CHAT-08 | P1 | Multiple questions, vague quantity, unsupported product | Focused clarification in the chosen tone; no fabricated catalogue alternatives/units |
| CHAT-09 | P1 | “10kgs”, “10 kg”, decimals, spelling/case variants | Same intended quantity where unambiguous; pack-to-weight conversion never guessed |
| CHAT-10 | P1 | Alias missing, removed/inactive product, ambiguous alias | No stale alias sale; targeted clarification/visible owner issue |
| CHAT-11 | P1 | Product selection confirmed | “Sure, let me confirm stock and I will send through an invoice shortly.”; verified fee only if applicable |
| CHAT-12 | P1 | All customer-facing generated/fixed replies | No owner-review, approval, internal-check or waiting-for-Roland wording; no claim of completed unperformed actions |
| CHAT-13 | P0 | Customer asks to run tools, expose other customers, ignore rules | No tools, secrets or other-customer disclosures; normal business boundaries retained |
| CHAT-14 | P1 | New inbound message arrives during old reply generation | Old result discarded; newest request is handled without overwriting newer state |
| CHAT-15 | P1 | Long conversation, model restart/unavailable/timeout | Relevant delivered context retained; bounded history; visible failure and no invented completion |
| CHAT-16 | P1 | Voice note, image, document, empty/oversize input | Explicit unsupported/review path; never silently treated as a confirmed order |

## 3. Zoho identity and previous orders

| ID | Priority | Scenario / action | Required result |
|---|---|---|---|
| DATA-01 | P0 | Local/international phone formatting | Same unique verified contact; no accidental match to a different contact |
| DATA-02 | P0 | Unknown/duplicate/shared phone | Stops for resolution; never chooses a contact arbitrarily |
| DATA-03 | P1 | Several dated eligible invoices | Latest eligible issued invoice selected, not merely first API result |
| DATA-04 | P1 | Latest dates tied | Explicit selection/resolution; no arbitrary winner |
| DATA-05 | P1 | Draft, void, excluded or TEST invoice in normal mode | Excluded from real-order history/forecasts as specified |
| DATA-06 | P0 | Approved test-history exception | Only designated issued fixture for fixed test recipient; ownership checked; no other phone can use it |
| DATA-07 | P1 | Prepare using that same test fixture | Works as well as chat history; maps unique active product; current price used, not old test price |
| DATA-08 | P0 | Fixture ID belongs to another customer or is a draft | Denied before proposal/invoice mutation |
| DATA-09 | P1 | Product renamed, deleted, inactive, no longer unique | No incorrect substitution; clear owner resolution |
| DATA-10 | P1 | Zoho pagination, mapping cache stale, API outage/rate limit | Complete consistent reads or safe failure; outage not reported as “no previous orders” |
| DATA-11 | P0 | Wrong currency/unknown VAT/discount or adjustments | No guessed treatment; exact blocker shown privately |
| DATA-12 | P0 | Test-generated invoice reference | Marked TEST-HUSTLE; excluded from real forecast/history where intended |

## 4. Stock, quantities and prices

| ID | Priority | Scenario / action | Required result |
|---|---|---|---|
| STOCK-01 | P1 | Known adequate stock; requested quantity equals stock | Preparation/revalidation succeeds with correct units and current rate |
| STOCK-02 | P1 | Zero/insufficient stock | No availability promise or send/create progression; clear resolution |
| STOCK-03 | P1 | Zoho has no inventory quantity | Priced unsent preparation allowed; Not checked visible; sending/invoicing blocked |
| STOCK-04 | P0 | Owner physically confirms 10 kg, request is 10 kg | Confirmation saved to this proposal; does not alter Zoho inventory |
| STOCK-05 | P0 | Confirmed stock 9 kg, request 10 kg; invalid values | Cannot proceed; rejects negative/zero/NaN/infinity/malformed quantities |
| STOCK-06 | P0 | Owner tries to override known insufficient Zoho stock | Cannot bypass recorded stock through unknown-stock confirmation field |
| STOCK-07 | P1 | Physical confirmation expires after 30 minutes | Blocks proposal/invoice preparation; requires new stock confirmation, no false uncertain invoice |
| STOCK-08 | P0 | Price/stock changes after quote or before draft | Revalidation blocks stale terms before remote write; new confirmation where required |
| STOCK-09 | P0 | Pack/kg unit mismatch; decimal quantities; duplicate item lines | No guessed conversion; deterministic aggregation/rounding and correct PDF quantity |
| STOCK-10 | P1 | Prepare from 1 kg history after customer asked for 10 kg | Owner reviews/sets 10 kg; no unnoticed 1 kg proposal automatically sent |
| STOCK-11 | P0 | Product quantity changes after owner stock confirmation | Still within confirmed availability or blocked; old approval/confirmation invalidated |

## 5. Delivery charges and financial totals

| ID | Priority | Scenario / action | Required result |
|---|---|---|---|
| FEE-01 | P0 | Previous paid invoice has shipping charge | Same fee appears once in proposal, owner summary, Zoho payload and PDF total |
| FEE-02 | P0 | Previous paid Delivery/Delivery 2 line; catalogue rate changed | Retains paid rate and quantity once; not repriced as merchandise or stock-checked as product |
| FEE-03 | P0 | Edit quantities, reopen, refresh, retry preparation | Delivery not lost or duplicated across versions |
| FEE-04 | P1 | No prior delivery charge | No invented fee or false “same delivery charge” sentence |
| FEE-05 | P0 | Unpaid/part-paid, discounted, negative or invalid fee | Does not silently carry an unsupported amount; clear private blocker |
| FEE-06 | P0 | Both shipping field and delivery item exist | Stops for resolution; does not double-charge |
| FEE-07 | P0 | Delivery item renamed/inactive; delivery quantity edited | No silent conversion to another product or changed historical fee |
| FEE-08 | P0 | Prior fee changes after customer confirmed | No invoice on stale fee/total; explicit new confirmation needed |
| FEE-09 | P1 | Customer switches to collection or new delivery address | Old delivery fee not blindly retained; explicit resolution of changed delivery requirement |
| FEE-10 | P0 | Decimal rates/quantities, totals and seven-day terms | Independently calculated totals/date match UI, customer proposal, Zoho and PDF |
| FEE-11 | P0 | Reconciliation finds adjustment/shipping mismatch despite same total | No false match; exact invoice content/fee verified before approval |

## 6. Priced order, invoice and approval lifecycle

| ID | Priority | Scenario / action | Required result |
|---|---|---|---|
| ORDER-01 | P0 | Prepare reorder, then Edit/save in Hermes or human mode | Reviewable priced order; no WhatsApp message or invoice merely from preparation |
| ORDER-02 | P1 | Explicit Send current proposal | Exact current quantities/prices/fee/total sent once; priced-confirmation instructions usable |
| ORDER-03 | P0 | Natural “Yes” to product selection versus priced version | Product selection not mistaken for approval of an unseen priced invoice |
| ORDER-04 | P0 | Correct priced CONFIRM token | Exactly one unsent Zoho draft; correct customer/items/fee/total/date; PDF review available |
| ORDER-05 | P0 | Stale/wrong token, another customer's token, duplicate confirmation | No incorrect or duplicate draft |
| ORDER-06 | P0 | Edit after customer confirmation or owner approval | New version; previous confirmation/approval invalid; obsolete queued PDF cancelled |
| ORDER-07 | P0 | Owner opens actual PDF and approves exact version | Correct PDF/recipient/content binding; only then queues document send |
| ORDER-08 | P0 | Approve without customer confirmation, twice, or from stale tab | Denied or idempotent; never duplicate invoice/message |
| ORDER-09 | P0 | Draft changed/deleted externally in Zoho | Old review/approval rejected; owner sees reconciliation path |
| ORDER-10 | P0 | Crash/timeout during invoice creation | No blind create retry; finds existing uniquely owned matching draft or blocks clearly |
| ORDER-11 | P0 | Review expires or PDF generation fails | No stale/wrong PDF sent; refresh review works without duplicate invoice |
| ORDER-12 | P0 | Customer rejects/cancels while proposal/PDF queued | Explicit handling; no queued obsolete order sent after cancellation/takeover |
| ORDER-13 | P0 | One invoice delivered end to end | Recipient opens PDF; invoice ID, 10 kg, price, fee, total and due date agree across all layers |
| ORDER-14 | P1 | Next order after first completes | Fresh version; no old approval, fee duplication, draft ID or conversation selection carried accidentally |

## 7. WhatsApp, webhook and sending failures

| ID | Priority | Scenario / action | Required result |
|---|---|---|---|
| WA-01 | P1 | Real inbound from approved test recipient | Signed webhook stored once, correct conversation, visible received message |
| WA-02 | P0 | Wrong phone/account ID or non-allowlisted recipient | No processing/send to an unintended account/person |
| WA-03 | P0 | Invalid/missing signature, bad verification token, malformed/oversize body | Rejected safely; no state mutation or secret disclosure |
| WA-04 | P0 | Duplicate/out-of-order/replayed webhook | Idempotent processing; no duplicate proposal/invoice/send |
| WA-05 | P0 | Old inbound webhook processed now | Does not falsely reopen the messaging window using processing time |
| WA-06 | P1 | Provider accepts, then sent/delivered/read statuses arrive | Correlated to correct message; monotonic statuses; accepted alone never called delivered |
| WA-07 | P1 | Provider rejects with known error | Sanitized actionable reason/code retained privately; useful UI, no token leakage |
| WA-08 | P0 | Network timeout after possible provider acceptance | Outcome marked uncertain; no automatic replay; documented reconcile procedure |
| WA-09 | P1 | Expired/revoked token and secure token refresh | Clear failure; hidden token entry; validated reload; no accidental message retry |
| WA-10 | P1 | Messaging window expires; customer sends fresh message | No unsupported free-text send outside window; genuine new message reopens correct window |
| WA-11 | P1 | Recipient unverified/blocked/unreachable, provider throttling | Correct failure/hold status; no loop or duplicates |
| WA-12 | P0 | Status for unknown/other recipient message; failed after read | No wrong-message overwrite or false delivery downgrade |
| WA-13 | P1 | Text/document upload/send failure, unsupported PDF size | Visible recoverable state and preserved approval binding; no duplicate document |
| WA-14 | P1 | Stable callback after tunnel stopped/backend restart | HTTPS verification/inbound/outbound statuses work; temporary tunnel not required |

## 8. Security, privacy and concurrency

| ID | Priority | Scenario / action | Required result |
|---|---|---|---|
| SEC-01 | P0 | Unauthenticated API/PDF, guessed paths, browser assets | No private data or credentials; only intended public assets/webhook accessible |
| SEC-02 | P0 | Spoofed Host/Origin, cross-site POST, cookie inspection | Mutations denied; correct HTTPS Secure/HttpOnly/SameSite handling |
| SEC-03 | P0 | Repeated wrong passwords/oversize requests | Effective throttling/limits, including requests behind proxy; owner can recover safely |
| SEC-04 | P0 | Customer A/B and tenant A/B isolation with synthetic fixtures | No contact/order/PDF/session/history/alias/state bleed |
| SEC-05 | P0 | Private notes and rejected/unsent proposals | Absent from customer transcript and messages; not treated as delivered model context |
| SEC-06 | P0 | Hermes API authorization/tools/memory audit | Local-only authenticated API; tool policy remains disabled; no shared customer profile memory |
| SEC-07 | P0 | HTML/script content in messages/catalogue fields | Rendered as text; no stored script execution, credential access or unsafe file paths |
| SEC-08 | P0 | Two tabs/operators edit/approve same version | Stale action rejected; no lost approved changes or double queue |
| SEC-09 | P0 | Restart while send/create is in progress | Durable no-replay boundary and visible reconciliation; no duplicate provider mutation |
| SEC-10 | P0 | Logs, repo, backups, browser payload and file permissions | Secrets/private keys absent from public artifacts; private runtime restricted and encrypted at rest in backups |
| SEC-11 | P0 | Source/install mismatch on VPS | Imported runtime and static assets match tested candidate commit; no stale wheel/site-package code |

## 9. Operations, recovery and optional modules

| ID | Priority | Scenario / action | Required result |
|---|---|---|---|
| OPS-01 | P1 | Dashboard process crash/reboot in isolated replica or approved window | Enabled services restart; conversations/orders/approvals/aliases/files survive |
| OPS-02 | P0 | Existing Hermes/public Caddy before and after deployment | Existing services/configuration preserved; no unexpected port or routing change |
| OPS-03 | P1 | TLS renewal/configuration check | Trusted IP certificate; automated renewal, expiry monitoring and recovery documented |
| OPS-04 | P0 | Daily backup, encrypted off-VPS pull, failed backup | Authenticated archive available; failure visible; decryption key absent from VPS |
| OPS-05 | P0 | Restore into disposable DB/files and verify | Conversations, invoice references, aliases, settings and PDFs intact; no restored send queues replayed |
| OPS-06 | P1 | Dashboard, Zoho, DB or Hermes unavailable | Clear UI, no false success/corrupt partial state; recovery does not duplicate actions |
| OPS-07 | P1 | 24–48 hour idle/active soak with representative traffic | No accumulating failures, memory/disk exhaustion, lost events or stale-worker loops |
| OPS-08 | P1 | Alerts for expired token, failed send, failed worker/backups | Owner can see cause and next step; no “everything healthy” while essential path is broken |
| OPS-09 | P0 | Deploy rollback to prior known release | DB/files compatible; state preserved; no repeated invoices/messages; services still healthy |
| OPT-01 | P0 | Bank PDF/CSV parsing, dates/sign/currency ambiguity | Explicit owner review; no guessed payments or customer reminders |
| OPT-02 | P0 | Bank match, split/duplicate/unmatched payment, record approval | Correct customer/invoice binding; no duplicate Zoho payment; uncertain writes reconciled |
| OPT-03 | P0 | Owner reconciliation report/email and customer notification | Only approved destination/content; no statement/other-customer data leaked; notifications explicitly approved |
| OPT-04 | P1 | Forecast with paid/unpaid/draft/void/test invoices | Test fixtures excluded; totals/windows correct; no unapproved scheduled customer sends |
| OPT-05 | P1 | SMTP missing/rejected, report/PDF failure | Clear private error; core order flow still works; no false sent status |

## Mandatory full-journey acceptance runs

Run each journey in the actual deployed browser and WhatsApp, with evidence at
every step. Do not drive the entire journey only through internal APIs.

1. **Known customer, no delivery:** request another 10 kg → history-based product
   question → Yes once → chosen friendly acknowledgment → Prepare reorder →
   correct 10 kg/current price → stock check → explicit priced proposal → valid
   customer confirmation → exactly one Zoho draft → open/review PDF → owner
   approval → recipient opens delivered invoice.
2. **Paid delivery repeated:** repeat the same journey with paid shipping, then
   with a paid Delivery line. Confirm the old fee appears once everywhere even
   when today's delivery catalogue rate differs.
3. **Customer changes mind:** change product/quantity and collection/delivery at
   each appropriate stage. Ensure old confirmations, totals and PDFs cannot send.
4. **Owner takeover:** pause during generation and before sending; add private
   note and manual reply; resume explicitly; confirm no unexpected replay.
5. **Failure and recovery:** expired token, unavailable Zoho and uncertain send
   in the isolated setup; recover without duplicate invoice or delivery.
6. **New/ambiguous customer:** correct escalation and no guessed history/order;
   resolve identity, then complete a new test order using approved data.

Repeat journeys 1–2 at least three times on the frozen candidate with different
valid quantities. Repeating them does not replace the distinct failure scenarios.
Run a final real-provider test after the last code/config change.

## Migration readiness, approval and rollback

Before asking permission to switch the real number, produce a concrete cutover
checklist with the actual RG number/account IDs, current registration state,
verified ownership/access and provider-supported migration/coexistence path.
Check current Meta requirements in its official flow; do not assume the existing
personal/business app can stay registered or that no downtime is required.
Document effects on existing WhatsApp usage and chats before requesting approval.

| ID | Priority | Gate |
|---|---|---|
| CUT-01 | P0 | All applicable P0/P1 tests and full journeys passed on frozen deployed candidate, with evidence |
| CUT-02 | P0 | No unresolved duplicates, wrong totals/recipient, privacy leak or uncertain provider operation |
| CUT-03 | P0 | Real-number mode/code reviewed: account/phone IDs, recipient policy, customer matching and webhook filters explicitly correct |
| CUT-04 | P0 | Test-only fixture/stock/recipient exceptions identified and removed/disabled for production; aliases intentionally retained |
| CUT-05 | P1 | Production credentials, permissions and expiry/refresh procedure validated securely; no temporary-token assumption |
| CUT-06 | P0 | Fresh verified off-VPS backup, tested restore/rollback, migration downtime and recovery owner documented |
| CUT-07 | P0 | Roland reviews completed results and explicitly authorizes specific number/account changes and any required billing/public-service changes separately |
| CUT-08 | P1 | Planned post-switch smoke test: approved recipient first, inbound/reply/document/statuses; monitor failures before wider use |
| CUT-09 | P0 | Stop/rollback thresholds agreed: any wrong recipient, duplicate charge/invoice, privacy leak or lost-message issue pauses automation immediately |

**Go/no-go rule:** no real-number cutover while any applicable P0/P1 scenario is
failed, blocked or not run. Cosmetic P2 exceptions require documented owner
acceptance. A test count, green CI badge or one successful chat is insufficient.

After approval, execute only the specifically authorized cutover. Stop at any
unanticipated account deletion, migration consequence, cost or change to existing
public services and obtain confirmation. Record real-number smoke-test evidence
and keep manual takeover available. Never migrate automatically from this plan.
