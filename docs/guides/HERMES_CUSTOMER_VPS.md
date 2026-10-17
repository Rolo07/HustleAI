# Connect the VPS Hermes customer profile

## Current deployment: approved existing-profile reuse

The owner selected the existing Hermes business profile and ChatGPT login for
HustleAI. Its existing `hermes-gateway` user service was restarted once with
explicit approval; the model, OAuth store, messaging configuration and CLI tools
were retained. There is no second provider sign-in and no credential cloning.

The business API listens only on `127.0.0.1:8643/v1`. Its API toolset list is
exactly `[no_mcp]`, which excludes both normal tools and default-enabled MCP
servers. Shared profile memory and user-profile memory are disabled. Authentication
and actual tool policy were checked, and a fictional model completion succeeded.
This changes shared memory behavior for the existing profile on every channel;
existing memory files were not deleted.

The dashboard uses the explicit `existing-restricted` connection mode. Default
behavior still requires the separate profile path; existing-profile mode accepts
only localhost `/v1/chat/completions` and still refuses missing/enabled tool
policies before sending customer text. Replies remain proposals requiring owner
approval. Different customers use separate session keys.

The unused separate `hustleai-hermes-customer` system service is stopped and
disabled. Its empty-login profile is preserved as an inactive alternative.
The previous customer sign-in helper now only reports existing-connection status.

Encrypted recovery backups now include the selected business profile's config,
API key and canonical OAuth store, rather than the unused profile. They exclude
live Hermes SQLite sessions. Archives are downloaded and authenticated on the
Mac using the existing backup key. Restore OAuth state only with the original
gateway stopped; a stale refresh grant can require a new sign-in.

The separate-profile instructions below describe the prepared alternative, not
the selected deployment.

## Deployed VPS customer account

The isolated dashboard deployment now has a dedicated `hustleai-customer` Unix
account. Its fresh `rg-midrand-customer` profile is under
`/var/lib/hustleai-customer/.hermes/profiles/rg-midrand-customer`, with a separate
system service, `hustleai-hermes-customer`. It uses the existing Hermes installation
and the owner's selected model/provider; no Hermes upgrade or owner-profile copy
was performed.

The API binds only `127.0.0.1:8643`. Verified checks confirm every reported toolset
is disabled, requests without its bearer key receive 401, and the default/owner
profile prefix receives 404. MCP servers, bundled skills, shared memory and user
profile memory are disabled. This Unix user cannot read the dashboard's database
credentials or the owner's Hermes home. The existing owner gateway is unchanged.

The customer profile needs an independent ChatGPT sign-in. Do not copy the owner
profile's OAuth credentials: independently refreshing a copied single-use grant
can disrupt the original session. From the Mac checkout run:

```sh
bash deploy/vps/connect-hermes-customer.sh USER@VPS
```

Follow the device-login URL and code in that private terminal. Never put login
codes or credentials in chat. The helper stops only the new customer service,
signs into its own profile, restarts it, verifies authentication and tool/profile
isolation, and requests a fictional model response. Only after a successful model
completion does it write the dashboard connection privately and restart the
dashboard. It also encrypts a customer-profile recovery backup. No WhatsApp reply
is sent by setup. If login is cancelled, run the same command again; the existing
owner gateway remains running.

Once setup reports success, send a fresh WhatsApp test message. Its reply should
appear as a proposal in the dashboard; approve that exact proposal to test send
and delivery. Previously processed failed events are not replayed automatically.

The new service has a 256 MiB memory ceiling and lower OOM priority to protect the
existing services on the small VPS. If a model request exceeds that limit, inspect
its service status before retrying. Do not restart/upgrade the owner gateway to
resolve a customer-service failure.

Customer recovery backups include only that profile's identity, configuration,
API key and provider credentials, encrypted with the Mac-held public-key backup
system. They exclude live Hermes SQLite sessions; customer messages remain in
the dashboard's PostgreSQL snapshot. The daily dashboard backup requires this
configuration backup to complete first. An off-VPS restore of old OAuth tokens
may require a fresh sign-in; never run two copies of one refresh grant.

The following manual instructions describe other installations, including a
dashboard that still runs on the Mac.

Keep your existing owner Hermes running. Use a separate profile for customer
wording, with no owner MCP servers or tools. Do not clone your owner profile:
cloning copies credentials, memories and plugins. Profiles isolate configuration,
but are not an operating-system sandbox. For stronger separation, run the customer
profile under a dedicated unprivileged VPS user.

These steps use the already-installed Hermes and your existing model provider.
They do not install software, open public ports, purchase services or change Meta.

## On your VPS

1. SSH in using your existing account. Create an empty profile:

   ```sh
   hermes profile create rg-midrand-customer
   hermes -p rg-midrand-customer model
   ```

   Select an existing provider/model. Enter provider credentials only in the VPS
   terminal's secure prompt. Do not copy the owner profile or its MCP configuration.

2. Open `~/.hermes/profiles/rg-midrand-customer/config.yaml` in your editor.
   Preserve the model settings and merge the following keys (avoid duplicate YAML
   keys). Disable every API-server toolset, including any installed plugin toolset:

   ```yaml
   mcp_servers: {}
   platform_toolsets:
     api_server: []
     cli: []
   agent:
     disabled_toolsets:
       - hermes-api-server
       - terminal
       - file
       - browser
       - web
       - code_execution
       - delegation
       - skills
       - memory
       - session_search
       - connections
       - cronjob
       - context_engine
   ```

   If your installed release uses different tool names, inspect them with
   `hermes -p rg-midrand-customer tools` and disable all API-server tools.
   The HustleAI connection check refuses any enabled or unreported toolset.
   Do not add owner skills, memories, connections or MCP servers.

3. In the **updated HustleAI checkout on the VPS**, generate a private API-server
   key without printing it:

   ```sh
   .venv/bin/python -m hustleai.dashboard.hermes_setup create-vps-key
   ```

   This writes `API_SERVER_ENABLED=true`, host `127.0.0.1`, port `8643` and a random
   `API_SERVER_KEY` to the customer profile's private `.env`. It refuses to overwrite
   an existing key. This is a Hermes API-server key, not your model provider key.
   If the checkout is only on your Mac, copy the reviewed updated project to your
   existing VPS project directory first; do not copy the Mac `.env` or `var/` data.

4. Start the customer profile's gateway:

   ```sh
   hermes -p rg-midrand-customer gateway start
   ```

   Confirm the customer API is listening on localhost port 8643. If your installed
   gateway multiplexes profiles into an existing process, consult its profile
   status/configuration before changing the owner gateway. Do not stop or
   reconfigure the owner agent just to make this work. Record the actual private
   port if it differs; the dedicated `/p/rg-midrand-customer/` URL is mandatory.

## On your Mac

1. Leave a terminal open with a private SSH tunnel (replace the account/address):

   ```sh
   ssh -N -L 18643:127.0.0.1:8643 YOUR_EXISTING_USER@YOUR_VPS_ADDRESS
   ```

   No VPS firewall port or public Hermes endpoint is needed.

2. Copy `API_SERVER_KEY` privately from the customer profile's `.env` using your
   local editor/secure SSH session. Do not paste it in chat, screenshots, command
   arguments or browser code. In the Mac HustleAI project run:

   ```sh
   .venv/bin/python -m hustleai.dashboard.hermes_setup configure-local
   ```

   Accept the default URL:
   `http://127.0.0.1:18643/p/rg-midrand-customer/v1/chat/completions`.
   Paste the key at the **hidden terminal prompt**. The helper authenticates to
   `/p/rg-midrand-customer/v1/toolsets`, verifies every reported toolset is disabled,
   then saves server-side settings in private `.env`. Nothing sends customer text
   during this check. On failure, no new connection settings are saved.

3. Verify and restart the live dashboard after configuration:

   ```sh
   .venv/bin/python -m hustleai.dashboard.hermes_setup check
   .venv/bin/python -m hustleai.dashboard.server --live --port 8091
   ```

   Stop the prior dashboard on this port first. Open http://127.0.0.1:8091 and use
   your existing owner password. Only one live dashboard worker may run per tenant.

If a step fails, share the step number and a redacted error, never a key. If the
VPS release lacks profile routing or the toolset endpoint, the adapter fails
closed; do not bypass its policy check. An upgrade requires your approval before
installation. A tool-free API policy prevents customer requests from calling owner
tools, but the VPS account still needs normal OS protection.

Official references: [Hermes profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)
and [Hermes API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server).

## Catalogue and last-order context for dashboard drafts

The dashboard loads current active catalogue names and the customer's last
eligible issued invoice before generating a reply. Customer phone matching and
invoice ownership checks use the existing Zoho workflow. Drafts, void invoices
and known tests are excluded normally; tied latest dates require owner resolution.
Only product names, quantities, units and the invoice date are passed as history,
not historical prices or unrelated customer details. Lookup failure does not
permit guessing or using another customer's records.

Private `customer-product-aliases.json` in the dashboard runtime directory maps
owner-confirmed customer phrases to Zoho item IDs. Only IDs still in the active
catalogue resolve. For example, the owner confirmed “chicken meal” refers to
“Rich in free range chicken”; the runtime file holds the ID, outside Git. The
reply proposes the exact catalogue product and asks for customer confirmation.
This does not confirm availability, convert pack sizes or create an order.

For the existing verified WhatsApp test recipient only,
`HUSTLEAI_TEST_HISTORY_INVOICE` can name an explicitly approved issued test
invoice. It is a read-only context fallback when ordinary lookup finds no
eligible history. It never permits drafts, overrides ambiguous customer matches
or tied real history, affects another phone, or changes quote/forecast rules.
Leave it empty outside that test. Runtime configuration is included in encrypted
private-file backups, not committed to Git.
