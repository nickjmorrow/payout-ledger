#!/usr/bin/env bash
#
# Wipe the public demo's database and bring it back freshly seeded. Runs on the
# server, nightly, from the cron file scripts/deploy.sh installs.
#
#   APP=payout-ledger /opt/payout-ledger/scripts/reset-demo.sh
#
# The console has no accounts, so anyone can authorize payments — which is the
# point of a demo, and means the fund drains and the lists fill up. A reset
# every night keeps the first thing a visitor sees the thing it should be.
#
# `down -v` removes this Compose project's volumes and nothing else: -p scopes
# it, so another project sharing the server keeps its database. The images are
# already built, so `up` is a restart, not a rebuild; the migrate container
# re-creates the schema and reseeds on the way up, exactly as on a first deploy.
#
# **It refuses to run unless SEED_DEMO_DATA=true.** That is the flag that says
# "this deployment is a demo", and the only thing standing between a cron line
# and deleting a real program's books every night at four.
set -euo pipefail

cd "$(dirname "$0")/.."
app="${APP:?APP must name the Compose project, as deploy.sh does}"

if ! grep -qx 'SEED_DEMO_DATA=true' .env.prod 2>/dev/null; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) refusing: .env.prod does not say SEED_DEMO_DATA=true, so this is not a demo" >&2
  exit 1
fi

compose="docker compose -p $app -f docker-compose.prod.yml --env-file .env.prod"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) resetting $app"
$compose down -v --remove-orphans
$compose up -d
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) reset $app"
