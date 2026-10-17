#!/bin/sh
# Installed root-owned as /usr/local/sbin/hustleai-customer-login on the VPS.
set -eu
systemctl stop hustleai-hermes-customer.service
runuser -u hustleai-customer -- env HOME=/var/lib/hustleai-customer HERMES_HOME=/var/lib/hustleai-customer/.hermes /usr/local/bin/hermes -p rg-midrand-customer auth add openai-codex --no-browser
systemctl start hustleai-hermes-customer.service
ready=false
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
    if curl --silent --fail --max-time 2 http://127.0.0.1:8643/health >/dev/null; then
        ready=true
        break
    fi
    sleep 2
done
if [ "$ready" != true ]; then
    echo 'Customer service is not ready; dashboard connection left unchanged.' >&2
    exit 1
fi
/opt/hustleai-dashboard/current/.venv/bin/python /opt/hustleai-dashboard/current/deploy/vps/complete_customer_setup.py
systemctl start hustleai-customer-backup.service
systemctl restart hustleai-dashboard.service
echo 'Hermes customer connection is ready. The existing owner Hermes gateway was not restarted.'
