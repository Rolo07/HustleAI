# Stable HTTPS using the VPS IP

The owner chose the existing VPS address instead of buying a domain:

- Dashboard: **https://142.93.175.92/**
- Stable Meta callback: **https://142.93.175.92/webhook**

The address remains stable while that public IP is retained. No domain, DNS
provider, paid tunnel, billing setup or real WhatsApp-number change is required.
A named business domain can be added later. The old localhost dashboard address
is superseded by the public HTTPS origin; the SSH connection remains available
for maintenance.

## Certificate and service

Let’s Encrypt now issues publicly trusted IP certificates using its `shortlived`
profile (160-hour certificates). Caddy manages issuance and renewal automatically
using TLS-ALPN-01 on port 443. No HTTP-01 route or public HTTP redirect was added
to the existing port-80 site. See [Let’s Encrypt's announcement](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability/).

The separate `hustleai-https` service uses the official, checksum-verified Caddy
2.11.7 binary at `/opt/hustleai-dashboard/tools/caddy-https`. It does not replace
`/usr/bin/caddy` or restart/reconfigure the original public Caddy service.

It runs as the unprivileged `hustleai-https` user, with only the capability needed
to bind port 443, a read-only system, private state and restart-on-failure. It is
enabled at boot. Its admin API is disabled. Certificate/account storage is private
under `/var/lib/hustleai-https`; public-host configuration is in
`/etc/hustleai-dashboard/https.env`. The Caddyfile and unit templates are in
`deploy/vps/https.Caddyfile` and `deploy/vps/https.service`.

The certificate was verified externally using normal trust and IP SAN checking;
no insecure TLS bypass or self-signed certificate is used. Future renewal depends
on this service remaining running and port 443 being reachable. Short certificates
must never be managed by an infrequent manual renewal process.

## Routes and authentication

Only exact required dashboard assets/API paths and `GET`/`POST /webhook` are
proxied to the existing backend on localhost port 8090. Other routes return 404,
including health, demo endpoints, private files and alternative webhook paths.
Webhook requests are limited to 1 MB; permitted dashboard POSTs to 8 MB, with
the backend's tighter endpoint limits still enforced.

The backend's private environment has
`HUSTLEAI_PUBLIC_ORIGIN=https://142.93.175.92`. Host and mutation Origin checks are
exact, owner cookies are Secure/HttpOnly/SameSite=Strict, and protected data still
requires owner authentication. No API/database/provider secrets enter browser
JavaScript. The existing owner password and Hermes connection are retained.

Checks passed for public root access, unauthenticated state denial, owner login,
secure cookie flags, authenticated state, invalid-origin rejection, blocked
routes, webhook verification, bad signatures and a valid signed empty payload.
Those synthetic webhook checks sent no WhatsApp message.

## Callback cutover

The owner approved the cutover: Meta verified the stable VPS callback, its active
`messages` subscription was read back, and a correctly signed empty webhook
returned HTTP 200. The temporary tunnel and filter are stopped and disabled.
Fresh WhatsApp traffic was confirmed by the owner after the switch.

For future callback changes, obtain the owner's
confirmation before replacing that existing Meta setting. Reuse the saved private
verification token and the existing `messages` subscription; verify/read back the
new callback and send Meta's synthetic webhook test. Retire the temporary tunnel
and its localhost filter only after the stable callback is verified.

Record the previous callback for rollback. Do not change the WhatsApp account,
register/migrate/delete a phone number, add billing or publish the Meta app as
part of this HTTPS change. The existing test number and verified personal test
recipient remain unchanged.

## Backup and recovery

The encrypted configuration backup also captures HTTPS certificate/account state
and the public-host environment file. Certificate/private-key pairs are checked
for consistency before accepting a snapshot; a concurrent rotation that produces
a mismatch fails the backup rather than saving a broken pair. These archives use
the existing Mac-held decryption key and off-VPS download job.

The daily PostgreSQL/private-file backup depends on that configuration backup.
Restore state with the HTTPS service stopped, with service-owned 0700 directories
and 0600 files. A certificate can also be reissued automatically after proving
control of the same IP. Keep the HTTPS binary/template and existing backend
source artifacts available separately.
