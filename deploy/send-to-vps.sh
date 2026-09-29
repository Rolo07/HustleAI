#!/usr/bin/env bash
# Run on the Mac, from the HustleAI folder. Copies RG Midrand's private files
# (never stored in Git) to the VPS for deploy/install.sh to pick up.
#
#   ./deploy/send-to-vps.sh root@<server-ip>
#
# The database admin login (.supabase-credentials.json) is deliberately NOT
# copied: the server doesn't need it.
set -euo pipefail

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  echo "Usage: $0 root@<server-ip>" >&2
  exit 1
fi
cd "$(dirname "$0")/.."

FILES=(.zoho-local.json tenant.json .zoho-credentials.json .supabase-runtime.json prod-ca-2021.crt)
[[ -f .whatsapp.json ]] && FILES+=(.whatsapp.json)
for f in "${FILES[@]}"; do
  [[ -f "$f" ]] || { echo "Missing $f in $(pwd). Nothing was copied." >&2; exit 1; }
done

echo "Copying ${FILES[*]} and invoice-pdfs/ to $TARGET:/root/rg-transfer ..."
ssh "$TARGET" 'mkdir -m 700 -p /root/rg-transfer'
scp -q "${FILES[@]}" "$TARGET:/root/rg-transfer/"
if [[ -d invoice-pdfs ]]; then
  scp -q -r invoice-pdfs "$TARGET:/root/rg-transfer/"
fi
ssh "$TARGET" 'chmod 600 /root/rg-transfer/* 2>/dev/null; chmod 700 /root/rg-transfer/invoice-pdfs 2>/dev/null; true'
echo "Done. On the server run:  sudo bash /opt/hustleai/deploy/install.sh"
echo "(or, before the code is on the server: curl -fsSLO https://raw.githubusercontent.com/Rolo07/HustleAI/main/deploy/install.sh && sudo bash install.sh)"
