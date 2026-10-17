#!/bin/sh
set -eu
host=${1:?Usage: refresh-meta-token.sh user@vps-host}
case "$host" in
  -*|*[!a-zA-Z0-9@._-]*) echo 'Invalid SSH host' >&2; exit 1 ;;
esac
exec ssh -t -o StrictHostKeyChecking=yes "$host" 'runuser -u hustleai-dashboard -- /opt/hustleai-dashboard/current/.venv/bin/python /opt/hustleai-dashboard/current/deploy/vps/refresh_meta_token.py && systemctl restart hustleai-dashboard.service'
