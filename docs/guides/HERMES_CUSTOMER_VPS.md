# Connect the VPS Hermes customer profile

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
