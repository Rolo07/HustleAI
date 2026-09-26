# WhatsApp integration (Meta Cloud API)

Transport only: settings, webhook signature checks and parsing, the Cloud API
client and the webhook HTTP server. Routing and permissions live in
`hustleai.workflows.messaging` and `hustleai.workflows.outbox`.

- `config.py`: loads `.whatsapp.json`. The business number is a setting.
- `webhook.py`: `X-Hub-Signature-256` verification, subscription check, event parsing.
- `client.py`: text, template and document messages; Meta error codes.
- `server.py`: stores each verified event before answering 200, processes in
  order on a worker thread and recovers unfinished events at startup.
- `owner_agent.py`: optional forwarder of Roland's free text to an
  OpenAI-compatible chat endpoint.

The owner MCP server must never be exposed to customer conversations. Only a
message from the owner number, verified from Meta's signed payload, can
confirm writes or approve invoices. See [the gateway guide](../../../../docs/guides/WHATSAPP_GATEWAY.md).
