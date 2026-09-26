# WhatsApp integration boundary (planned)

Integrate with Hermes's Meta Cloud API gateway. Keep provider transport details
(signature verification, incoming event parsing, document upload and verified
message receipts) here when custom adapters are needed; do not duplicate native
Hermes support without a reason.

Business routing, enquiries, reorders, referrals and daily summaries belong in
`hustleai.workflows`. Customer and owner conversations need separate permissions.
The current `hustleai.mcp.owner_server` must never be exposed to customer sessions.
Only owner approval of the current invoice version can authorize delivery.

This folder is a documented boundary, not an implemented WhatsApp connector.
