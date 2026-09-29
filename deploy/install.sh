#!/usr/bin/env bash
# HustleAI installer for an Ubuntu VPS (target: Ubuntu 24.04). Needs nothing
# from any other computer: it asks for every ID, key and secret as it goes.
#
#   curl -fsSLO https://raw.githubusercontent.com/Rolo07/HustleAI/main/deploy/install.sh
#   sudo bash install.sh
#
# Have these ready (a phone browser is enough):
#   - Zoho: organization ID, and a Self Client at https://api-console.zoho.com
#   - Supabase: Session pooler connection string and the database password
#   - An API key for the AI model Hermes will use (skipped if Hermes is set up)
#   - Meta WhatsApp: phone number ID, permanent access token, app secret
#   - A domain name pointing at this server
# Safe to run again: finished steps are skipped and the code is updated.
# Works on a server that already runs Hermes: it reuses that user and install,
# and asks before changing the firewall, timezone or an existing web server.
set -euo pipefail

REPO="https://github.com/Rolo07/HustleAI.git"
APP=/opt/hustleai
DATA=/var/lib/hustleai
TENANTS=$DATA/tenants
SLUG=rg-midrand
BIN=$APP/.venv/bin

step() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }
ask()  { local answer; read -r -p "$1" answer </dev/tty; printf '%s' "$answer"; }
confirm() { [[ "$(ask "$1 [y/N] ")" =~ ^[Yy] ]]; }

[[ $EUID -eq 0 ]] || die "Run as root: sudo bash install.sh"
command -v apt-get >/dev/null || die "This installer supports Ubuntu/Debian only."

# Reuse the user that already runs Hermes, if any; otherwise create "hermes".
EXISTING=()
while IFS=: read -r name _ _ _ _ home _; do
  if [[ -n $home && -d $home/.hermes ]]; then EXISTING+=("$name"); fi
done < <(getent passwd)
if [[ ${#EXISTING[@]} -eq 1 ]]; then
  USER_NAME=${EXISTING[0]}
  echo "Hermes is already installed for user '$USER_NAME'; HustleAI will run as that user."
elif [[ ${#EXISTING[@]} -gt 1 ]]; then
  USER_NAME=$(ask "Hermes exists for several users (${EXISTING[*]}). Which one should run HustleAI? ")
else
  USER_NAME=hermes
fi
[[ -n $USER_NAME ]] || die "No user chosen."

user_home() { getent passwd "$USER_NAME" | cut -d: -f6; }

# Run a command as the service user with its own home, PATH and systemd user bus.
as_user() {
  local uid home; uid=$(id -u "$USER_NAME"); home=$(user_home)
  (cd "$home" && sudo -u "$USER_NAME" -H env \
    HOME="$home" \
    PATH="$home/.local/bin:$BIN:/usr/local/bin:/usr/bin:/bin" \
    XDG_RUNTIME_DIR="/run/user/$uid" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
    HUSTLEAI_TENANTS_ROOT="$TENANTS" \
    "$@")
}

step "1/7 System packages, timezone and firewall"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3-venv python3-dev git curl build-essential >/dev/null
# HTTPS: use Caddy unless another web server already holds port 80 or 443.
OTHER_WEB=$(ss -ltnpH '( sport = :80 or sport = :443 )' 2>/dev/null | grep -v caddy || true)
if [[ -n $OTHER_WEB ]]; then
  PROXY=manual
  warn "Another web server is using port 80/443, so Caddy won't be installed."
else
  PROXY=caddy
  apt-get install -y -q caddy >/dev/null
fi
ZONE=$(timedatectl show -p Timezone --value)
if [[ $ZONE != Africa/Johannesburg ]] && confirm "Server timezone is $ZONE. Change it to Africa/Johannesburg (recommended for the schedules)?"; then
  timedatectl set-timezone Africa/Johannesburg
fi
if command -v ufw >/dev/null; then
  if ufw status | grep -q "Status: active"; then
    ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null
    ok "Firewall already on; opened ports 80 and 443."
  else
    SSH_PORT=$(ss -ltnpH 2>/dev/null | grep -m1 sshd | awk '{print $4}' | sed 's/.*://' || true)
    if confirm "Turn on the firewall allowing only SSH (port ${SSH_PORT:-22}), 80 and 443?"; then
      ufw allow "${SSH_PORT:-22}/tcp" >/dev/null && ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null
      ufw --force enable >/dev/null
    fi
  fi
fi
ok "Packages installed; timezone $(timedatectl show -p Timezone --value)."

step "2/7 Service user and folders"
id "$USER_NAME" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$USER_NAME" >/dev/null
loginctl enable-linger "$USER_NAME"
USER_HOME=$(user_home)
GROUP_NAME=$(id -gn "$USER_NAME")
mkdir -p "$APP" "$TENANTS"
chown "$USER_NAME:$GROUP_NAME" "$APP" "$DATA" "$TENANTS"
chmod 700 "$DATA" "$TENANTS"
ok "User $USER_NAME; code in $APP; private data in $DATA."

step "3/7 HustleAI code"
if [[ -d $APP/.git ]]; then
  as_user git -C "$APP" pull --ff-only
else
  as_user git clone -q "$REPO" "$APP"
fi
[[ -x $BIN/python ]] || as_user python3 -m venv "$APP/.venv"
as_user "$BIN/pip" install -q --upgrade pip
as_user "$BIN/pip" install -q -e "$APP[postgres,hermes]"
grep -q "$BIN" "$USER_HOME/.bashrc" 2>/dev/null || \
  echo "export PATH=$BIN:\$PATH HUSTLEAI_TENANTS_ROOT=$TENANTS" >> "$USER_HOME/.bashrc"
ok "Installed $(as_user git -C "$APP" log --oneline -1)."

step "4/7 Business details, database and Zoho"
TENANT_DIR=$TENANTS/$SLUG
echo "Answer the questions below. Secrets are typed at hidden prompts."
as_user "$BIN/hustleai-tenant" bootstrap "$SLUG" </dev/tty
as_user env HUSTLEAI_DATA_DIR="$TENANT_DIR" "$BIN/hustleai-orders" status || warn "Orders not synced yet; the nightly job will do it."

step "5/7 Hermes Agent"
if as_user bash -c 'command -v hermes' >/dev/null; then
  ok "Using the Hermes already installed for $USER_NAME."
  if grep -qs '^WHATSAPP' "$USER_HOME/.hermes/.env"; then
    warn "Hermes has its own WhatsApp settings. Don't use RG Midrand's business number there:"
    warn "the HustleAI gateway must own that number's webhook."
  fi
  if confirm "Choose Hermes's AI model/provider again?"; then
    as_user hermes model </dev/tty
  fi
else
  echo "Installing Hermes (its own setup may ask you questions)..."
  as_user bash -c 'curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash' </dev/tty
  as_user bash -c 'command -v hermes' >/dev/null || die "Hermes did not install. Check the output above, then rerun this installer."
  as_user hermes model </dev/tty
fi
as_user "$BIN/hustleai-tenant" hermes-install "$SLUG"
as_user hermes gateway install </dev/tty || warn "hermes gateway install failed; run it later as $USER_NAME."
ok "Hermes profile $SLUG ready."
as_user hermes -p "$SLUG" cron list || true

step "6/7 WhatsApp"
if [[ -f $TENANT_DIR/.whatsapp.json ]] && ! confirm "WhatsApp is already set up. Change it?"; then
  ok "Keeping the existing WhatsApp settings."
elif confirm "Do you have the Meta details now (phone number ID, access token, app secret)?"; then
  NUMBER=$(ask "Business WhatsApp number (e.g. +27821234567): ")
  PHONE_ID=$(ask "Meta phone number ID (digits): ")
  as_user "$BIN/hustleai-whatsapp" setup --tenant "$SLUG" --business-number "$NUMBER" --phone-number-id "$PHONE_ID" </dev/tty
  as_user "$BIN/hustleai-whatsapp" check --tenant "$SLUG" || warn "Meta check failed; fix the settings and rerun."
  as_user "$BIN/hustleai-tenant" hermes-install "$SLUG"   # connects WhatsApp chats to Hermes
else
  warn "Skipped. Rerun this installer when you have the Meta details."
fi

step "7/7 Gateway service and HTTPS"
if [[ -f $TENANT_DIR/.whatsapp.json ]]; then
  sed "s/^User=hermes$/User=$USER_NAME/" "$APP/deploy/schedule/hustleai-gateway.service" \
    > /etc/systemd/system/hustleai-gateway.service
  systemctl daemon-reload
  systemctl enable --now hustleai-gateway >/dev/null
  systemctl restart hustleai-gateway
  DOMAIN=$(cat "$DATA/domain" 2>/dev/null || true)
  [[ -n $DOMAIN ]] || DOMAIN=$(ask "Domain pointing at this server (e.g. hustle.yourdomain.co.za): ")
  echo "$DOMAIN" > "$DATA/domain"
  if [[ $PROXY == caddy ]]; then
    CADDY=/etc/caddy/Caddyfile
    BLOCK=$(sed "s/hustleai.example.com/$DOMAIN/" "$APP/deploy/Caddyfile.example")
    if grep -qs 'HustleAI' "$CADDY"; then
      ok "Caddy already has the HustleAI site."
    elif [[ -f $CADDY ]] && ! grep -qs 'file_server' "$CADDY"; then
      # A customised Caddyfile: keep its sites and add ours after a backup.
      cp "$CADDY" "$CADDY.bak.$(date +%s)"
      printf '\n# --- HustleAI ---\n%s\n' "$BLOCK" >> "$CADDY"
    else
      # Only Ubuntu's default welcome page: replace it.
      printf '# --- HustleAI ---\n%s\n' "$BLOCK" > "$CADDY"
    fi
    caddy validate --config "$CADDY" --adapter caddyfile >/dev/null 2>&1 || die "Caddy config invalid; check $CADDY (a backup was kept)."
    systemctl reload caddy || systemctl restart caddy
  else
    warn "Configure your existing web server to forward https://$DOMAIN/webhook* and /health"
    warn "to http://127.0.0.1:8085 (see $APP/deploy/Caddyfile.example)."
  fi
  sleep 3
  if curl -fsS "https://$DOMAIN/health" >/dev/null 2>&1; then
    ok "https://$DOMAIN/health is ok."
  else
    warn "https://$DOMAIN/health not reachable yet. Check DNS points here and the web server forwards to port 8085."
  fi
  VERIFY=$(python3 -c "import json;print(json.load(open('$TENANT_DIR/.whatsapp.json'))['verify_token'])")
  printf '\n\033[1mIn the Meta app (WhatsApp > Configuration):\033[0m\n'
  echo "  Callback URL:  https://$DOMAIN/webhook/$SLUG"
  echo "  Verify token:  $VERIFY"
  echo "  Subscribe to:  messages"
  echo "Then send HELP to the business number from your phone."
else
  warn "Gateway not started: WhatsApp isn't set up yet."
fi

printf '\n\033[1;32mInstall finished.\033[0m\n'
echo "Logs:     journalctl -u hustleai-gateway -f   |   $TENANT_DIR/reports/jobs.log"
echo "Update:   sudo bash $APP/deploy/install.sh"
