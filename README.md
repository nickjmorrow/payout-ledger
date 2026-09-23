# Ledger

A disbursement service for unconditional cash transfers. A programme sends money
to recipients through a mobile-money provider, and the books stay correct while
that happens — including when the provider times out, a worker dies mid-payment,
or the same request arrives twice.

Postgres + FastAPI + React + a worker. Four containers, one command.

```bash
docker compose up
```

Then open <http://localhost:3001>. The programme starts with an opening balance
and 24 recipients. Authorise a disbursement and watch it move: **pending**
while the fund is debited and nobody has been paid yet, **processing** once the
provider accepts it, **succeeded** when they confirm the money arrived.

Watch "Available to disburse" drop the moment you authorise, while "Held at
provider" does not. That gap is the point — the money has been promised and has
not moved — and both fall together a few seconds later when it settles.

## What it actually demonstrates

The domain is small. The failures it survives are not.

- **Double-entry bookkeeping**, with the balance rule enforced by a deferred
  constraint trigger in Postgres rather than by application code, and
  `ledger_entries` append-only by another. Corrections are reversing entries, so
  both the mistake and the fix stay in the history.
- **Idempotency keys** on every mutating request. Retrying a disbursement is
  safe; reusing a key with a different body is refused rather than silently
  replayed.
- **A Postgres-backed queue** — `FOR UPDATE SKIP LOCKED`, `LISTEN`/`NOTIFY`, and
  a sweeper for workers that die mid-task. No Redis, no broker.
- **The outbox pattern**, which needs no extra machinery here because the queue
  is a table: a transfer, its journal, its idempotency record and the job that
  will send it all commit together or not at all.
- **Retries with exponential backoff and a dead-letter queue**, where exhausting
  the retries reverses the transfer rather than leaving money promised to
  somebody who will never receive it.
- **A reconciliation pass** that compares the books against the provider's own
  records and repairs the drift that is safe to repair, flagging the rest for a
  person.
- **Write skew, prevented.** Two concurrent transfers that each check the
  balance and each see enough will both post and overdraw the fund. Every
  journal balances; no constraint objects. There is a test that reproduces it.
- **Payment runs, all or nothing.** Pay every enrolled recipient in one
  request: one transaction authorises every transfer or none, checked against
  the fund for the whole total. Two workers claim the sends with `SKIP LOCKED`
  and the console shows which worker holds which.
- **Live updates over Server-Sent Events**, fed by `NOTIFY` from inside the
  transaction that made each change, so the console is never told about a
  payment it cannot yet read. A notice says *what* changed and the browser
  re-reads it, so a lost frame costs a moment of staleness, never a wrong number.

## What the console shows

- **The books for any transfer.** Click a recipient to see its journal entries
  as a bookkeeper would lay them out — debits, credits, totals that match — and
  every attempt the worker made. A failed transfer shows the authorisation and
  its reversal side by side; nothing was deleted.
- **The queue as it works.** Due, scheduled, running and dead work, with the
  worker holding each task and a countdown to the next one.
- **The dead-letter queue, with a Retry** that refuses to re-run a payment
  that already finished, and says why.

[AGENTS.md](./AGENTS.md) is the argument behind all of it — read
[§ The books](./AGENTS.md#the-books) before changing anything in `services/`.

## Trying the interesting parts

```bash
# Retry a disbursement with the same key: one payment, replayed response.
curl -s -X POST localhost:8001/api/transfers \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: try-this-once' \
  -d '{"recipientId":"<id>","amountMinor":250000,"currency":"KES"}' -D-

# Break the provider and watch the transfer reverse itself rather than
# stranding the money. Three hooks, all off by default.
PROVIDER_UNREACHABLE=true docker compose up -d worker      # retried, then reversed
PROVIDER_REJECT_ALL=true docker compose up -d worker       # reversed immediately
PROVIDER_FAIL_SETTLEMENT=true docker compose up -d worker  # accepted, then lost

# The books, from psql. This is always zero.
docker compose exec db psql -U app -d app -c \
  "select sum(case when direction='debit' then amount_minor else -amount_minor end) from ledger_entries;"
```

| Service | URL |
| --- | --- |
| Console | <http://localhost:3001> |
| API | <http://localhost:8001> |
| API docs | <http://localhost:8001/docs> |
| Postgres | `localhost:5434` (`app` / `app` / `app`) |

## Working on it

```bash
scripts/setup.sh           # once per clone: deps, .env, and the pre-commit hook
scripts/check.sh           # lint, format, types and tests, both halves
scripts/check.sh --fast    # the subset the hook runs — no database, no bundle
```

One script, three callers: you, the pre-commit hook, and CI. A hook that checks
something different from CI is worse than no hook.

Most of the test suite needs a real Postgres, deliberately: `FOR UPDATE SKIP
LOCKED` means nothing without concurrent transactions, a deferred trigger means
nothing without a COMMIT, and `pg_notify` delivers nothing inside a transaction
that always rolls back. A mocked database would test none of the things most
likely to be wrong.

Some conventions are checked by **structural tests** — `backend/tests/structure/`
and `frontend/src/structure.test.ts` — which assert on the shape of the codebase
rather than on what it computes: that only `config.py` reads the environment,
that nothing outside `app/provider/` touches the provider's table, that routes
do not build queries. [AGENTS.md § Checks](./AGENTS.md#checks) lists them all.

```bash
# Change the schema: edit backend/app/models.py, then generate a revision.
# Read it before you commit it — autogenerate cannot see a trigger.
docker compose run --rm migrate alembic revision --autogenerate -m "what changed"

# Start over from an empty database (destroys data)
docker compose down -v && docker compose up

# What is queued right now
docker compose exec db psql -U app -d app \
  -c "select kind, status, attempts, error from tasks order by created_at desc limit 10;"
```

## Deploying it

```bash
cp .env.prod.example .env.prod          # fill in every value; none default
docker compose -f docker-compose.prod.yml --env-file .env.prod up --build -d
```

| | Development | Production |
| --- | --- | --- |
| Frontend | Vite dev server | Built bundle on nginx, which also proxies `/api` |
| Reload | Hot, with source mounts | None; the image is the artifact |
| Backend user | root | Unprivileged `app` (uid 10001) |
| Healthchecks | db only | All four, worker included |
| Workers | One | Two, so `SKIP LOCKED` is contended for real |
| Postgres port | Published on 5434 | Not published at all |
| Demo data | Seeded | Off — the chart of accounts only |
| Logs | Human-readable | JSON |

### The public demo

```bash
scripts/deploy.sh root@203.0.113.5 ledger.203-0-113-5.sslip.io
```

One server over SSH, safe to re-run. It installs Docker and Caddy, writes a
`.env.prod` with a generated password and demo data on, builds, puts Caddy in
front for HTTPS (with `flush_interval -1`, or the live updates arrive in lumps),
and publishes nginx on `127.0.0.1` only — on `0.0.0.0` the plain-HTTP port would
be reachable past the firewall, because Docker writes its own iptables rules.
Everything it creates is named after the app, so it shares a server with other
projects deployed the same way.

**The demo resets every night at 04:17.** The console has no accounts, so anyone
can authorise payments. `scripts/reset-demo.sh` drops this project's database
volume and brings it back freshly seeded — and refuses to run unless `.env.prod`
says `SEED_DEMO_DATA=true`, which is the only thing between a cron line and
deleting a real programme's books every night.

**The nginx proxy is not decoration.** The client calls `/api` on its own origin,
and in development Vite's dev server proxies that to the backend — a `server:`
block that does not exist in a built bundle. Without something reproducing it, a
production build has no route to the API at all.

**The migrate step seeds as well as migrating, and that is not optional.** The
chart of accounts — a programme funding account and a provider settlement
account — has to exist before any transfer can be authorised, so a stack that
skipped it would start cleanly and then refuse every disbursement. The seeder is
idempotent. `SEED_DEMO_DATA` defaults to false here and true in development:
sample recipients and an opening balance are exactly what a demo wants and
exactly what a real deployment does not.

Deliberately not included, because they depend on where you deploy: TLS
(terminate it at your platform's load balancer, or put Caddy in front), secrets
management, log shipping, and backups.

## Project layout

```
backend/          FastAPI app + worker — see AGENTS.md § Layout
backend/alembic/  Migrations; versions/ is the schema's history
frontend/         The disbursement console
scripts/          setup.sh and check.sh — the only two you run by hand
AGENTS.md         The conventions, and the reasoning. The actual deliverable.
```

## What's deliberately not here

Pagination, rate limiting, multi-currency FX, provider webhooks, an approval
workflow, batch disbursement. Each is cheap to add later and expensive to build
before you need it, and
[AGENTS.md § What is deliberately missing](./AGENTS.md#what-is-deliberately-missing)
says when to add each and which seam is already waiting.

Also not here, for a different reason: the chaos pass, the ten-million-row scale
test, and the `EXPLAIN ANALYZE` tuning exercise. The hooks for breaking the
provider exist in `config.py`; the rest is work whose value is in doing it.
