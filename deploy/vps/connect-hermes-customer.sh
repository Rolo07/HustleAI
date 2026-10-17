#!/bin/sh
set -eu
host=${1:?Usage: connect-hermes-customer.sh user@vps-host}
case "$host" in
  -*|*[!a-zA-Z0-9@._-]*) echo 'Invalid SSH host' >&2; exit 1 ;;
esac
exec ssh -t -o StrictHostKeyChecking=yes "$host" '/usr/local/sbin/hustleai-customer-login'
