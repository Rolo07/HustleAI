#!/bin/sh
# Replaces the sign-in helper after the owner selects existing-profile reuse.
set -eu
echo 'The existing Hermes login is reused; a separate customer sign-in is not needed.'
cd /var/lib/hustleai-dashboard
exec runuser -u hustleai-dashboard -- /opt/hustleai-dashboard/current/.venv/bin/python -m hustleai.dashboard.hermes_setup check
