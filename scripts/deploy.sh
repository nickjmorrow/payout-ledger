#!/usr/bin/env bash
#
# Deploy the public demo to one server over SSH. Safe to re-run: every step
# checks before it acts, so the first run sets the server up and every later
# run is a redeploy.
#
#   scripts/deploy.sh root@203.0.113.5                          # https://203-0-113-5.sslip.io
#   scripts/deploy.sh root@203.0.113.5 ledger.203-0-113-5.sslip.io
#   scripts/deploy.sh root@203.0.113.5 ledger.example.com       # DNS already pointed
#
# Ported from the same script in clinical-copilot, and built to share a server
# with it. Everything this creates is named after APP (default: this folder's
# name) — its directory, its Compose project and therefore its containers,
# network and database volume, its local port, its Caddy site file and its cron
# file — so neither project can overwrite the other. Without domains they cannot
# both have the bare sslip.io name, so give this one a prefix, as above.
#
# On a fresh Ubuntu 24.04 server, the first run:
#   1. installs Docker and Caddy from Ubuntu's own packages, adds swap, and opens
#      only SSH, HTTP and HTTPS;
#   2. writes .env.prod there: a generated database password, a free local port,
#      and SEED_DEMO_DATA=true, because this is the public demo;
#   3. copies the code (rsync, so later runs send only what changed), builds and
#      starts — the migrate container migrates and seeds on the way up;
#   4. puts Caddy in front, which gets and renews the certificate itself;
#   5. installs a nightly reset (scripts/reset-demo.sh), because anyone can
#      authorise payments on a console with no accounts.
set -euo pipefail

target="${1:?usage: [APP=name] scripts/deploy.sh user@host [domain]}"
host="${target#*@}"
domain="${2:-${host//./-}.sslip.io}"

cd "$(dirname "$0")/.."

app="${APP:-$(basename "$PWD")}"
if [[ ! "$app" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "APP must be lowercase letters, digits and dashes, not '$app'." >&2
  exit 1
fi
remote_dir="/opt/$app"
# -p overrides the `name:` inside the compose file, so two projects copied from
# one template can never share a Compose project and replace each other.
compose="docker compose -p $app -f docker-compose.prod.yml --env-file .env.prod"
ssh_opts=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)

say() { printf '\n\033[1m── %s\033[0m\n' "$*"; }
remote() { ssh "${ssh_opts[@]}" "$target" "$@"; }

say "Server: $target  ·  App: $app  ·  Site: https://$domain"

say "Provisioning"
remote 'bash -s' <<'PROVISION'
set -euo pipefail
if ! command -v docker >/dev/null || ! command -v caddy >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -yq docker.io docker-compose-v2 docker-buildx caddy rsync ufw
  systemctl enable --now docker
  if ! swapon --show | grep -q .; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
  ufw allow OpenSSH
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw --force enable
else
  echo "already provisioned"
fi

mkdir -p /etc/caddy/sites
if ! grep -qxF 'import /etc/caddy/sites/*.caddy' /etc/caddy/Caddyfile 2>/dev/null; then
  if [[ -f /etc/caddy/Caddyfile ]]; then cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak; fi
  echo 'import /etc/caddy/sites/*.caddy' > /etc/caddy/Caddyfile
fi
PROVISION

say "Configuration"
if remote "test -f $remote_dir/.env.prod"; then
  echo ".env.prod is already on the server — left exactly as it is"
else
  # The first local port no other project on this server has claimed or is
  # listening on. Recorded in this project's .env.prod and kept from then on.
  free_port="$(remote 'bash -s' <<'PORT'
taken="$(cat /opt/*/.env.prod 2>/dev/null | sed -n 's/^PUBLIC_PORT=//p')"
for port in $(seq 8080 8179); do
  if ! grep -qx "$port" <<<"$taken" && ! ss -Hltn "sport = :$port" | grep -q .; then
    echo "$port"
    exit 0
  fi
done
exit 1
PORT
)"

  # umask 077: the file holds the database password.
  remote "mkdir -p $remote_dir && umask 077 && cat > $remote_dir/.env.prod" <<ENV
POSTGRES_USER=app
POSTGRES_PASSWORD=$(openssl rand -hex 24)
POSTGRES_DB=app
PUBLIC_ORIGIN=https://$domain
# Caddy, on this machine, is the only thing that reaches nginx.
PUBLIC_BIND_ADDRESS=127.0.0.1
PUBLIC_PORT=$free_port
LOG_LEVEL=info
# The public demo: recipients and an opening balance, reset nightly. Also the
# flag scripts/reset-demo.sh checks before it deletes anything.
SEED_DEMO_DATA=true
ENV
  echo "wrote .env.prod (local port $free_port; database password generated; demo data on)"
fi
port="$(remote "sed -n 's/^PUBLIC_PORT=//p' $remote_dir/.env.prod")"

say "Copying the code"
# .env.prod is excluded so --delete can never remove the server's copy. Only
# options old enough for macOS's openrsync.
rsync -az --delete --stats -e "ssh ${ssh_opts[*]}" \
  --exclude .git --exclude .env --exclude .env.prod --exclude .DS_Store \
  --exclude node_modules --exclude .venv --exclude dist \
  --exclude __pycache__ --exclude .pytest_cache --exclude .ruff_cache \
  ./ "$target:$remote_dir/"

say "Building and starting (the first build takes a few minutes)"
remote "cd $remote_dir && $compose up --build -d --remove-orphans"

say "HTTPS"
# This project's site file only, so deploying it never touches another's.
# flush_interval -1 passes Server-Sent Events through as they are written —
# without it the console's live updates arrive in lumps, or not at all.
remote "cat > /etc/caddy/sites/$app.caddy && caddy validate --config /etc/caddy/Caddyfile >/dev/null && systemctl reload caddy" <<SITE
$domain {
	reverse_proxy 127.0.0.1:$port {
		flush_interval -1
	}
	header Strict-Transport-Security "max-age=31536000"
}
SITE

say "Nightly reset"
# 04:17 server time: after midnight everywhere this is likely to be looked at
# from, and off the hour, when every other cron job on earth runs.
remote "cat > /etc/cron.d/$app-reset" <<CRON
17 4 * * * root APP=$app $remote_dir/scripts/reset-demo.sh >> /var/log/$app-reset.log 2>&1
CRON
echo "installed /etc/cron.d/$app-reset"

say "Checking"
for _ in $(seq 1 36); do
  if curl -fsS --max-time 5 "https://$domain/api/health" >/dev/null 2>&1; then
    echo "Live: https://$domain"
    exit 0
  fi
  sleep 5
done
echo "Not answering on https://$domain after 3 minutes." >&2
echo "Caddy's log: ssh $target journalctl -u caddy -n 50" >&2
exit 1
