# Isolated VPS dashboard deployment

The dashboard can run alongside an existing Hermes installation. Inventory
running services, listening ports, firewall rules, Python, existing checkout,
tenant folders and reverse proxy before installing anything. Preserve existing
Hermes profiles, public sites and their configuration. The broad original
`deploy/install.sh` is not used for this isolated deployment.

## Layout and access

The owner subsequently approved reusing the existing Hermes business profile and
ChatGPT login. Its private API is tool-free, shared memory is disabled, and the
unused separate customer service is stopped/disabled. See the current deployment
section in [the Hermes connection guide](HERMES_CUSTOMER_VPS.md).

- `/opt/hustleai-dashboard/releases/<release>`: root-owned source and virtualenv.
- `/opt/hustleai-dashboard/current`: selected release symlink.
- `/opt/hustleai-dashboard/python`: separate managed Python, leaving OS and Hermes
  interpreters untouched. Install the reviewed version and audit dependencies.
- `/var/lib/hustleai-dashboard`: service-owned mode 0700 private runtime folder.
  The `.env`, OAuth credentials and restricted database settings have mode 0600.
  Administrative Supabase credentials are never copied to the VPS.
- `/etc/systemd/system/hustleai-*`: separate dashboard, webhook filter, temporary
  tunnel and backup units from `deploy/vps/`.

Build/install application code with umask 022 so the unprivileged service can
read it; use umask 077 for private runtime/backup files. A package reinstall under
077 can make new package directories unreadable to the service. Check imports
and health as the actual service user before considering an update complete.

The dashboard binds only `127.0.0.1:8090`. Use private SSH access while no domain
is configured:

```sh
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 127.0.0.1:8090:127.0.0.1:8090 USER@VPS
```

Open `http://127.0.0.1:8090` on that computer and enter the owner password.
The deployment Mac also has a reconnecting `com.hustleai.vps-dashboard-tunnel`
login service for this forward; stop it before running a second tunnel on 8090.
The connection to the VPS is encrypted by SSH. The browser-to-loopback hop uses
HTTP; this is private access, not a public HTTPS dashboard deployment.

For a future HTTPS dashboard, set `HUSTLEAI_PUBLIC_ORIGIN=https://YOUR-DOMAIN`
without a trailing slash, forward the original Host, and proxy only the static
files and necessary API routes. Exact HTTPS Origin checks and Secure session
cookies are enabled by that setting. Do not trust client-supplied forwarded host
or protocol headers to determine the origin. Confirm changes to an existing
public proxy before applying them.

PostgreSQL continues to use the existing TLS-verified Supabase database and
restricted runtime role; there is no new local database or fallback. The existing
restricted role is reused for this initial deployment; it has not been rotated
or reassigned to a new VPS-only role. A credential leak on either installation
therefore affects the same role. No role ownership mapping is changed.
Required PDFs and reports are copied privately. Historical review paths are not
rewritten in the shared database; generate a fresh review when an old Mac path
is unavailable. Stop the Mac dashboard worker before starting the VPS worker.
Do not activate duplicate schedules on both machines. Forecast/order-sync
schedules are not installed by these dashboard units.

## Services and the temporary Meta callback

The dashboard service runs as a separate unprivileged `hustleai-dashboard` user,
with a read-only system, private temp directory, no home access, restrictive
umask, restart-on-failure and CPU/memory limits. Only its private runtime folder
is writable. Units are enabled at boot. Secrets are loaded server-side from the
private working directory's literal `.env`, never from shell command arguments.

`hustleai-webhook-filter` runs a separate Caddy process with its admin API disabled
and a separate configuration, bound only to `127.0.0.1:8087`. Its only permitted
route is exact `GET`/`POST /webhook`, with a 1 MB request limit. All other paths
return 404. It does not load, reload or modify the existing public Caddy service.

`hustleai-webhook-tunnel` provides temporary HTTPS via an official checksum-verified
cloudflared binary. It forwards only to that route filter, uses outbound
connections and opens no inbound firewall ports. Its metrics listen on localhost.
The dashboard, login, private APIs and health route are not exposed by the tunnel.

**The temporary hostname changes after a tunnel restart and has no uptime
guarantee.** Record the current URL from the tunnel journal and reverify Meta's
callback after a restart. This is a test endpoint. A stable production callback
requires a domain or managed tunnel setup and a reviewed cutover.

Before changing Meta, inspect the current callback, obtain confirmation when
required, and preserve its previous value for rollback. Verify the callback,
subscribe the `messages` field and check the test WABA subscription separately.
The live dashboard restricts the transport to its existing designated Meta test
phone ID and verified test recipient. It does not register or migrate a real
number.

Refresh an invalid/expired test token with a hidden prompt:

```sh
bash deploy/vps/refresh-meta-token.sh USER@VPS
```

Generate a fresh token in the existing HustleAI app's WhatsApp test setup first.
The helper validates the test phone metadata before saving the token atomically
and restarting only the dashboard. It preserves the app secret and owner password.
Never paste tokens in chat. A callback verification token is not an access token.

The Meta app's inspected configuration on 17 October 2026 states that unpublished
apps receive only dashboard-generated test webhooks. However, the later genuine
verified-recipient test reply reached the VPS and was processed, and correlated
delivered/read statuses arrived. The observed test-account behavior contradicts
that blanket UI notice; publishing was not required for this verified test. Do
not infer production-number eligibility from that result. Do not claim a phone roundtrip until
a genuine inbound event, correlated outbound message ID, delivery/read status and
phone receipt have all been verified. Publishing, permissions, billing and real
number migration require separate review/confirmation where applicable.

## Encrypted backups and recovery

`hustleai-backup.timer` runs daily at 01:00 UTC (03:00 South African time), with up
to five minutes of randomized delay and catch-up after downtime. It creates:

- A consistent repeatable-read, read-only snapshot of all accessible private
  tables with the tenant's organization ID, including the owner dashboard.
- The service's private runtime files, PDFs, reports and credentials.
- A manifest of table counts and SHA-256 file digests.

The archive is encrypted using AES-256-GCM; the fresh symmetric key is wrapped
with RSA-OAEP/SHA-256. Only the public RSA key is installed on the VPS. Encrypted
archives live under `/var/backups/hustleai-dashboard/encrypted`.

The owner's Mac stores the private key and downloaded encrypted archives under
`~/.local/share/hustleai-vps-backups` with owner-only permissions. An hourly
`com.hustleai.vps-backup-pull` launch agent downloads new archives over verified
SSH and authenticates/decrypts each new download in memory before accepting it.
Existing archives are retained; automatic pruning is not configured.

Off-VPS downloads require the Mac to be awake, logged in, online and its existing
SSH key unlocked. The captured SSH-agent socket may change after a Mac restart;
update the launch agent's socket environment or run the pull helper from a terminal
with the unlocked agent. Failure appears in `pull-error.log`; do not assume that
local VPS backups alone protect against losing the VPS. Back up the Mac-held
decryption key separately using the owner's existing encrypted backup system.
The pull job's isolated Python environment is stored in the same private backup
folder, so it does not require background access to the Desktop checkout.

Recover first into an isolated database and private staging folder. Decrypt with
the Mac-held key, validate the manifest, apply matching schema migrations and
restore typed rows in dependency order. Backup JSON contains data, never executable
SQL. Do not overwrite a live database or replace its role mapping as a test.
Stop workers before any approved recovery cutover; reconcile attempted external
writes and invalidate old approvals before resuming automation.

Validation for this deployment included authenticated archive decryption and all
file digests, plus typed restoration of 13 tables using temporary PostgreSQL tables
inside rolled-back transactions. This verifies row/type roundtripping, not a full
disaster recovery of schema, grants, constraints, services or an entire Supabase
project. The source release/migrations must be preserved separately. Supabase
platform backup/PITR coverage has not been independently verified.

## Validation and rollback

Check owner login, protected state/catalog/report reads, exact-origin rejection,
Meta verification, invalid-signature rejection, signed payload acceptance and
denial of public dashboard/API paths. Verify runtime file permissions and private
socket bindings. Test restart-on-failure on the dashboard alone and confirm state
survives. Never reboot the VPS or restart Hermes as a deployment test.

To stop this deployment, stop/disable only the new `hustleai-dashboard`,
`hustleai-webhook-filter`, `hustleai-webhook-tunnel` and `hustleai-backup.timer`
units. Preserve private state and backups. A code rollback switches `current` to
a previously verified compatible release and restarts only the new dashboard.
The old `/opt/hustleai` checkout and original public Caddy configuration remain
available. Revert any approved Meta callback change separately. Run only one
dashboard worker per tenant when returning to the Mac.
