# Dashboard browser and live checks — 11 October 2026

Tested the running local site with Chrome on desktop (including 1440px) and
390px phone viewport. Provider mutations used the explicit fictional demo only.

Passed browser checks:

- Search reduced the inbox to the matching customer; Needs approval filtered to
  the missing-record escalation.
- Edited chicken from six to eight packs; total recalculated from R376 to R440.
  The previous version was invalidated and approval remained disabled.
- Confirmed the new exact version via the demo incoming-message control; an
  unsent draft appeared, then the approval dialog displayed recipient, total and
  exact version. Approving queued a single simulated PDF; the view reported
  API acceptance with delivery still unconfirmed and disabled repeat approval.
- Takeover enabled manual replies; explicit Resume Hermes restored automatic
  mode and disabled the manual reply composer.
- Internal notes appeared separately and never entered the outbound message
  queue. A manual demo reply appeared separately with its send state.
- Orders and Activity views loaded; audit entries included edits, customer
  confirmation, draft creation, approval, takeover, note and resume.
- Mobile navigation opened and selected Activity; the 390px viewport had no
  horizontal overflow. Restored the normal desktop viewport afterwards.

Found and fixed: opening a different customer from an order/review card reused
the prior composer text. Drafts now stay in memory per customer and separately
for internal notes/customer replies. Opening review cards also marks the
conversation read. Verified that Daniel's private draft stayed out of Maya's
composer and the customer reply composer, and returned when Daniel's internal
note composer reopened. Logout clears drafts. Refresh intentionally clears
unsent in-memory drafts.

Live checks (no customer messages/orders sent):

- Health 200.
- Unauthenticated state/catalog endpoints 401.
- Authenticated owner login 200; live state 200, demo false, zero conversations.
- Authenticated Zoho catalog 200, 23 active products.
- The 19 focused dashboard/Hermes-policy tests passed. JavaScript syntax and Git
  whitespace checks passed after the UI fix.

The earlier full suite result remains 157 run / 121 passed / 36 skipped. Skipped
external integration tests are not a claim of live end-to-end success. Hermes
customer configuration, authoritative stock availability, and Meta inbound
publication/subscription remain blockers to the real WhatsApp reorder journey.

Chrome logged extension messaging errors ('Receiving end does not exist'); the
site workflows above completed successfully. No application error banner appeared
in the tested successful workflows.

Screenshot: ignored local `var/dashboard-site-tested.png` (fictional data only).
