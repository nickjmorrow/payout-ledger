# Conventions

This file is the argument behind the code. It is not a tour of the codebase —
the modules have docstrings for that — it is the set of decisions that are
expensive to rediscover, and the reasons they went the way they did.

**Where a rule here has teeth, something checks it.** Prose is enforced by
nothing, which is not a hypothetical worry: two rules in the project this was
forked from were written down from the start, never checked, and quietly broken
for months. The structural tests in `backend/tests/structure/` and
`frontend/src/structure.test.ts` exist for that reason. [§ Checks](#checks)
lists them.

Read [The books](#the-books) before changing anything under
`backend/app/services/`. Everything else in this system exists to keep that
part honest.

## What this is

A disbursement service for unconditional cash transfers: a programme sends
money to recipients through a payment provider, and the books stay correct
while that happens.

The domain is small on purpose. What is not small is the set of failures it has
to survive — a provider that times out, a worker killed mid-payment, a duplicate
request, a settlement that never arrives — because those are what separate a
system that moves money from one that stores rows.

**The provider is a mock** (`app/provider/mock.py`), pretending to be somebody
else's company so the whole thing runs with `docker compose up`. It is reached
through a Protocol, exactly as a real integration would be.

| Layer | Choice |
| --- | --- |
| Database | Postgres 17 |
| Backend | FastAPI, uvicorn, SQLAlchemy 2.0 async, asyncpg |
| Migrations | Alembic |
| Queue | Postgres — `FOR UPDATE SKIP LOCKED` + `LISTEN`/`NOTIFY`. No Redis. |
| Frontend | React 19, Vite, TypeScript, Tailwind v4, TanStack Query |
| Tests | pytest against a real Postgres; vitest. No network. |
| Types | basedpyright strict on `app/`, tsc |

## Running it

```bash
docker compose up          # db, migrate+seed, backend, worker, frontend
```

Then <http://localhost:3001>. The programme is seeded with an opening balance
and 24 recipients; authorise a disbursement, or a payment run to all of them,
and watch it settle.

`scripts/setup.sh` once per clone, `scripts/check.sh` for everything.

## The books

**Every movement of money is a journal entry whose lines sum to zero.** Not an
update to a balance — a pair of postings, a debit and a credit, recorded and
never changed. That is what makes the books self-checking: if debits and credits
ever stop agreeing you have a bug and you find out immediately, instead of
discovering a silent miscalculation months later with no way to tell when it
started.

### The invariants live in Postgres

Two triggers, created in the `ledger schema` migration:

- **`ledger_entries_balance`** — every journal balances, in one currency, across
  at least two lines. A `DEFERRABLE INITIALLY DEFERRED` constraint trigger,
  because a journal is unbalanced between its first INSERT and its last. COMMIT
  is the first moment at which "does this balance" is even a well-formed
  question; an immediate check would reject every correct posting ever made.
- **`ledger_entries_append_only`** — refuses UPDATE and DELETE. A mistake is
  corrected by posting a *reversing* entry, which leaves both the error and the
  correction in the history. An UPDATE would leave neither. Not deferred: this
  one is violated by a single statement, so failing there points at the line
  that did it.

**They are in the database because the application is not the only writer.** A
psql session, a data fix, a future service in another language, and a bug in
our own code all bypass a check written in Python. None of them bypass these.

**Alembic cannot see triggers.** It diffs tables, indexes and constraints, so if
one of these is ever dropped no future `--autogenerate` will mention it. The
migration says so at its top. `ledger_service.trial_balance()` is the canary:
debits minus credits across the whole ledger, always zero, and surfaced in the
console. A non-zero value there means a trigger has gone missing rather than
that a posting was wrong.

### Balances are derived, never stored

There is no `balance` column on `accounts`, and adding one would be the single
most damaging change available. A stored balance is a second source of truth
that can disagree with the entries, and having exactly one is the entire reason
to keep books this way.

When that stops performing, the fix is a rollup table maintained in the same
transaction as the entries — a cache, which can be rebuilt from the entries and
checked against them. Not a mutable column that becomes the truth by default.

### Direction is the one thing you must simply know

`NATURAL_DIRECTION` in `ledger_service.py`: assets rise on a debit, liabilities
and equity rise on a credit. It is written down once so nothing else has to
remember, and so `balance()` can return numbers that read the way a person
expects — a fund with money left in it is positive.

**Getting a direction backwards is the only mistake that still balances.** No
constraint will ever object, because the journal is perfectly valid. The tests
that catch it assert on what the numbers *mean* — that authorising reduces the
money available to give away — rather than that rows exist. Write that kind of
test when you add a posting.

### One way in

Every write to `ledger_entries` goes through `ledger_service.post`. Not because
the database would accept a bad journal otherwise — it would not — but because
a caller assembling lines by hand has to get the directions right, and `post`
takes both halves together and refuses anything else. Its Python checks
duplicate the triggers deliberately: they are not the enforcement, they are the
error message. Where the two disagree, the database is right.

Currency is a parameter of the journal rather than of each line, which makes the
mixed-currency journal the trigger refuses unrepresentable in the first place.

## The transfer lifecycle

A transfer has two lives. The `transfers` row is the **intent** and its
progress: mutable, with a status. The journal entries are **what happened**:
immutable. Keeping them separate is what lets a transfer fail and be retried
without the books ever showing a payment that did not occur.

| Status | Means | Posting |
| --- | --- | --- |
| `pending` | Authorised. The fund is debited, the recipient is owed. | `transfer_authorized` |
| `processing` | The provider has accepted it. | none — the books already say enough |
| `succeeded` | They confirm the recipient was paid. | `transfer_settled` |
| `failed` | It will not happen. | `transfer_reversed` |

**Authorising debits the fund before the money moves.** That is the conservative
direction: the programme must not be able to promise the same dollar twice
while a payment is in flight. It comes back on reversal.

### Concurrency: the failure no constraint catches

Two transfers, started at the same moment, each reading the fund balance and
each seeing enough. Both post. Every journal balances, the trial balance is
zero, and the fund is overdrawn. That is **write skew**, and nothing about
either transaction is individually wrong.

`transfer_service.initiate` takes a row lock on the funding account *before*
reading the balance. The lock protects no data — nothing updates that row — it
is a mutex, and the account row is the agreed place to take it. Locking
`ledger_entries` cannot work: the rows the other transaction is about to write
do not exist yet, so there is nothing there to lock.

Verified by deleting the lock and watching
`test_concurrent_transfers_cannot_overdraw_the_fund` report
`['authorised', 'authorised']`. Do that again if you change this.

### Payment runs

A run is many transfers authorised as one decision (`run_service`), and it is
**all or nothing**: every transfer or none. That is not a policy on top of the
transfers — it is what doing the whole run in one transaction means. N calls to
`transfer_service.initiate`, one commit. A run that fails on its seventh
recipient leaves no trace of the first six. Partial runs sound kinder and are
worse: an operator told "twenty-three of forty authorised" has to work out which
seventeen to retry against a fund that moved underneath them, and the chance of
paying someone twice is the chance they get that list wrong.

The fund is checked **for the run's total**, under the funding lock, before
anything is posted. Each `initiate` would refuse the transfer that tipped the
fund over anyway, but with an error about one recipient when the truth is about
the total. `test_two_runs_at_once_cannot_overdraw_the_fund` is the write-skew
test a run at a time.

**A run has no status and no total.** Both are counted from its transfers when
asked, for the reason balances are: a stored status is a second record of what
the transfers already say, and would one day say a run was finished with
payments still in flight. The row holds only what the transfers cannot — that
they were decided together.

## Idempotency

**`POST /api/transfers` and `POST /api/runs` require an `Idempotency-Key`
header.** Not optional. An endpoint that moves money and accepts a keyless
request will eventually pay somebody twice, and the client is the only party
that knows two requests are the same request. A run raises the stakes: a
timed-out run retried without its key authorises every payment in it again.

The HTTP half — four service answers into four responses — is
`api/idempotent.py`, written once for both endpoints. The 409 carries
`Retry-After` on the exception rather than on the injected `Response`, because
FastAPI discards the latter when a handler raises. It had been set there, and
never reached a client, until a test asked for it.

`idempotency_service.claim` is `INSERT ... ON CONFLICT DO NOTHING`, not a SELECT
followed by an INSERT. The read-then-write version has a window in which two
requests both see nothing and both do the work; the whole point of the module is
that that window does not exist. **The uniqueness constraint is the lock** — no
advisory lock, no extra round trip. A concurrent request blocks on the first
transaction's uncommitted row and then either replays it or takes the key,
depending on whether the first committed.

Four answers, not two. `Proceed` and `Replay` are obvious; the other two matter
more:

- **`InFlightError`** → 409. The honest answer is to ask again shortly, not a
  guess at what the other request will return.
- **`KeyConflictError`** → 422. The key was used for a *different* body.
  Replaying the first response would silently discard this request, which is
  exactly the failure the mechanism exists to prevent: the client would be told
  its payment succeeded when the payment it asked for was never made.

A replay returns the **stored** response, not one re-derived from the current
transfer. Two identical requests must not give two different answers even if the
transfer has moved on since.

The browser holds one key per attempt and reuses it across retries
(`hooks/useDisburse.ts`, `hooks/useCreateRun.ts`), regenerating only on success.
A fresh key per retry would defeat the entire mechanism.

## The provider seam

`app/provider/base.py` is the contract; `mock.py` is the only implementation and
the only thing that may touch `provider_payments`. Two structural tests hold
this, because the moment a service reads that table directly, reconciliation
starts comparing our records to our records and passes for the wrong reason.

Three properties of the mock are deliberate and must survive a real integration:

- **It runs on its own session, not the caller's.** A real provider does not
  enlist in your transaction — it commits when it commits, and your rollback
  does not unsend a payment.
- **It settles lazily, on time passing.** Nothing in the application can *cause*
  a settlement. Code that could would pass here and fail against a real
  integration.
- **It dedupes on the idempotency key we supply** and returns the original
  payment for a repeat. That is what makes at-least-once delivery tolerable: we
  may send twice, the recipient is paid once.

`ProviderError.retryable` is the adapter's judgement and nobody else's, because
only the adapter knows whether a failure is a timeout worth another attempt or a
rejection that never will be. Marking everything retryable burns a payment's
whole budget on something that cannot succeed; marking nothing retryable drops
payments over a blip.

The three failure hooks in `config.py` default to off. They exist so a chaos
pass has somewhere to plug in; they are not themselves that pass.

## The worker

**Payments are not sent in the HTTP request.** `POST /transfers` authorises and
enqueues; a separate process — `app/worker/`, its own container — sends and then
settles.

Keep the worker in its own process. Not a thread, not a `create_task` in the
API. The moment the thing doing the work can see the thing serving the request,
cancellation stops needing to travel through the database and a dead worker
stops needing to be noticed by somebody else — and both of those problems are
still there, just no longer expressible.

### Postgres is the queue

Three pieces, all in `services/task_service.py`:

- **Claiming** is `select … for update skip locked`. Two workers take different
  rows instead of queueing behind each other. This is the property the whole
  design rests on: if it silently stops holding, two workers run one payment.
- **Waking** is `NOTIFY` on one global channel.
- **Sweeping** is what makes the claim safe. A worker killed mid-task leaves a
  row marked `running` forever, because nothing in Postgres knows the worker
  existed. The sweeper is that knowledge. It is not optional.

### The outbox

**`enqueue` and `bus.publish` do not commit.** Callers do.

A disbursement writes a transfer, posts a journal, records an idempotency key
*and* asks for a payment to be sent. All four have to land together. If enqueue
committed, the job would be durable before the rows describing it were, and a
crash in that window leaves a worker holding a job whose subject does not exist.

Writing the job into a table inside the caller's transaction is the outbox
pattern, and it needs no extra machinery here because the queue already *is* a
table. The NOTIFY is safe for a subtler reason: **NOTIFY is transactional.**
Postgres holds it until COMMIT and discards it on ROLLBACK, so publishing before
committing cannot wake a listener about a row that is still invisible — or still
hypothetical.

### Retries, and the dead-letter queue

Exponential backoff, capped, in `retry_delay_seconds`. A task that exhausts
`max_attempts` is parked as `failed`, and **that is the dead-letter queue** —
not a second table, because a failed row already carries the attempt count, the
error and the payload, and moving it elsewhere would only lose them.

**The DLQ must not leak money.** A parked task whose transfer still said
`pending` would mean the fund is debited and nobody will ever be paid. The last
attempt reverses the transfer before giving up — `_handle_provider_error` in
`worker/disburse.py`. If you add a task kind that moves money, do the same.

**One path still breaks that rule, and it is known.** The sweeper dead-letters a
task whose worker *died* on its last attempt, in SQL, knowing nothing about
transfers — so `_handle_provider_error` never runs, and the transfer stays
`pending` with the fund debited. Until that is fixed automatically, a person
fixes it from the console: the dead-letter queue has a Retry.

**Retry is a judgement, and `dead_letter_service` makes it.** A task about a
transfer that already settled or was reversed is refused — running it again
would do nothing, and would look to an operator like a second payment. A task
about an unfinished transfer is retried, safely, because both halves check
before acting: the handler skips a transfer that is no longer pending, and the
provider dedupes on the transfer id. Retry extends the budget
(`max_attempts += 3`) rather than zeroing `attempts`, so how the task got to
the dead-letter queue stays readable. It needs no idempotency key: the row is
locked and a task that is no longer dead is refused, so a repeat is refused,
not doubled.

### Recurring work runs once

Reconciliation is one task that schedules its own successor, and every
scheduling of it goes through `task_service.schedule_once`: a *running* pass
counts as scheduled, and the check-then-insert is serialised by an advisory
lock. It used to count only pending passes, and with two workers that seeded a
second chain whenever one worker's loop looked while the other was mid-pass —
chains that then ran side by side forever. One duplicate pass is harmless; a
duplicate chain grows without bound. It was spotted on the console's queue
panel. Recurring work you add goes through `schedule_once` too.

### Asking, not waiting

`settle_transfer` polls and reschedules itself while the answer is still
`pending`. **A slow payment is an answer, not an error**: the retry budget is
for errors, and spending it on a payment that is merely slow would abandon a
transfer that was going to settle perfectly well.

Polling rather than a webhook. A webhook is an optimisation on top of this,
never a replacement — a callback that is never delivered leaves a payment in
flight forever, and the only thing that finds it is somebody asking.

### Checking before acting

The status checks at the top of each handler are the most important lines in
`worker/disburse.py`. A worker killed after the provider accepted a payment but
before the row was updated comes back and runs from the top. The provider would
dedupe it — that is what the key is for — but **relying on the other side to
catch our mistake is not a design.**

## Reconciliation

Everything above is built so that a single failure cannot lose or duplicate
money. Reconciliation exists because that is not enough. It catches the failures
that happen *between* the guarantees — a send whose response we never saw, a
settlement we stopped polling for, a payment they have and we do not. Those
leave no error anywhere.

**Heal only where the provider is authoritative and we are merely out of date.
Where the two records genuinely contradict each other, a person decides.**

| Finding | Meaning | |
| --- | --- | --- |
| `status_behind` | They settled, we were still polling. | **Healed** |
| `status_contradicted` | They say failed, we say paid. | Reported |
| `missing_at_provider` | We believe we sent it, they have no record. | Reported |
| `unknown_to_us` | A payment matching no transfer. | Reported |
| `amount_mismatch` | Same payment, different amount. | Reported |

`status_behind` healing is what makes the job worth *running* rather than only
worth alerting on. `status_contradicted` does not heal because the settlement
journal has already moved money, and unwinding that on one disagreement is how a
provider glitch becomes a reversal storm. `missing_at_provider` does not heal
because our send failing and their record being lost need opposite responses,
and a machine cannot tell which from here.

**A finding is a fact about a moment, not a ticket.** Nothing updates one when
the problem is fixed. A later pass that still sees it writes another row; one
that does not writes nothing, and the absence is the record of the resolution.
That is what keeps "what did we know, and when" intact after the fix.

The pass reschedules itself through the same queue as everything else, and
enqueues its successor in the same transaction that records its findings. A
crash between the two would otherwise stop reconciliation forever, and "the job
that finds problems is the problem" is a bad failure mode.

## Live updates

The console hears about changes as they commit, over one Server-Sent Events
stream (`GET /api/events`), and re-reads what they affect.

**A notice, not the data.** Every frame is `{topic, id, transferId}`: a
transfer, a task or a finding moved, and which one. The browser then re-reads
through the ordinary endpoints. So a frame can be dropped, reordered or never
sent, and the worst outcome is a moment of staleness. Anything a viewer could
not recover by re-reading a table does not belong on this channel.

**Announced inside the transaction that made the change.** `bus.announce` is a
`pg_notify` on the caller's session, so it inherits the property the outbox
already relies on: delivered at COMMIT, discarded on ROLLBACK. The console is
never told about a transfer it cannot yet read, or about one that never
happened. Every state change in `transfer_service`, `task_service` and
reconciliation announces; a new one should too.

**`ready` is sent after LISTEN is in place, and the browser re-reads everything
on it.** That ordering is the whole argument for reconnecting safely. Headers
go out before the subscription exists, so refreshing when the socket opens
would leave a window where a change is seen by neither the refresh nor the
stream.

**Silence is death.** The server heartbeats every 10s and the browser drops a
connection that has been quiet for 25s, then reconnects with backoff. A stream
can die without closing: Vite's dev proxy keeps the browser's side open after
the backend is gone, and the console said Live while hearing nothing, until the
watchdog. Laptop sleep and NAT timeouts look the same. Change the heartbeat
and the stall together.

**`fetch`, not `EventSource`**, because `EventSource` cannot send a header, and
the auth seam puts a bearer token on every request. `sse.ts` parses the stream.

**Polling is the fallback.** While the stream is live, a 60s backstop remains
to bound how long a lost notice can matter. While it is not, the console polls
at the old cadence. Either way, every view refreshes in the same moment: the
overview is re-read with every change, which is what keeps a settled transfer
from sitting beside a float balance from before it settled.

**No database session per stream.** Every open console shares the process's
one LISTEN connection. Uvicorn runs with `--timeout-graceful-shutdown`, because
it otherwise waits on every open console before restarting, and one open tab
made every `--reload` hang forever.

## Layout

```
backend/alembic/       Migrations. versions/ is the schema's history.
backend/tests/         pytest. unit/, integration/, structure/, support/.
frontend/nginx.conf    Serves the built bundle and proxies /api in production.
backend/app/
  main.py              App wiring only. No logic.
  config.py            All environment reads. The only place os.getenv belongs.
  logging.py           structlog setup + the logging rules.
  db.py                Async engine and session factory.
  models.py            SQLAlchemy models. THE SCHEMA'S SOURCE OF TRUTH.
  wire.py              The camelCase convention, and the change-notice frame.
  seed.py              Chart of accounts (required) + demo data (optional).
  bus.py               LISTEN/NOTIFY fan-out, and `announce`.
  api/
    deps.py            Shared dependencies, including the auth seam.
    middleware.py      Request ids and access logging. Pure ASGI.
    schemas.py         The {data, meta} envelope + the HTTP-only shapes.
    idempotent.py      Four idempotency answers into four HTTP responses.
    routes/            Thin: validate -> authorize -> call a service -> respond.
  services/
    ledger_service.py        Posting, balances, the chart of accounts.
    transfer_service.py      The disbursement lifecycle.
    run_service.py           Payment runs: many transfers, one decision.
    dead_letter_service.py   Whether a dead-lettered task may run again.
    idempotency_service.py   Making a repeated request one request.
    reconciliation_service.py  Comparing our books to the provider's.
    recipient_service.py     Recipients.
    task_service.py          The queue: claim, settle, sweep, dead-letter.
  provider/
    base.py            THE SEAM. The Protocol and its types.
    mock.py            Pretending to be somebody else's company.
    registry.py        Which provider this process uses.
  worker/
    __main__.py        `python -m app.worker`.
    loop.py            Sweep, seed reconciliation, drain, wait for NOTIFY.
    handlers.py        The task-kind registry and the settle path.
    disburse.py        disburse_transfer, settle_transfer.
    reconcile.py       The reconciliation pass, rescheduling itself.
    shutdown.py        The stop flag and the signal handlers that set it.
frontend/src/
  main.tsx             Mounts App inside its providers.
  App.tsx              The console: one page.
  index.css            Tailwind + the @theme block. The only stylesheet.
  api/                 The HTTP boundary and the event stream. No React.
  money.ts             Minor units in and out. No React.
  events.ts            Which queries a change notice re-reads. No React.
  sse.ts               The event-stream parser. No React.
  polling.ts           The fallback refetch cadence. No React.
  labels.ts            Schema identifiers as words. No React.
  runs.ts              A payment run's progress, in words. No React.
  queue.ts             What an unfinished task is doing, in a phrase. No React.
  format.ts            Timestamps and durations. No React.
  hooks/               Stateful logic that isn't layout. One hook per file.
  components/          UI. One per file, default export, named after the file.
```

**The rule for where code goes:** if a second caller would need it — a CLI, the
worker, a test — it belongs in `services/`. Routes exist to translate HTTP into
a service call and back.

### Which direction imports run

Backend, top to bottom. Each layer may import the ones below and never above:

```
main.py, worker/          the two processes: wiring and the run loop
api/routes/               HTTP. Imported by main.py alone.
services/                 business logic. Imports models, provider, bus, config.
provider/, bus.py         the seams and the fan-out
wire.py, models.py, db.py, config.py, logging.py
```

Two rules with teeth: **`services/` never imports `api/` or `worker`**, and
**nothing outside `app/api/` imports `app.api` at all, except `main.py`.** A
service that reaches up into the HTTP layer is one the worker can no longer
call, which is the entire reason `services/` exists.

**Imports are absolute** (`src/api/client`), never relative.

### One thing per file, named after the file

A component file exports its component and nothing else. Every `__init__.py` is
empty. Services are imported as modules — `from app.services import
ledger_service`, then `ledger_service.post` — so the call site says which layer
it is calling into.

### Where tests go

Backend tests split by what they need: `unit/` and `structure/` need nothing,
`integration/` needs a Postgres. That split is not tidiness — `scripts/check.sh`
runs the first two on every commit and the third only when a database is
reachable, so the directory *is* the interface.

Frontend tests sit beside what they test. A top-level `.ts` module with no
neighbouring test is visible at a glance, and a structural test fails on it.

**Most tests here need a real Postgres, and that is correct.** `FOR UPDATE SKIP
LOCKED` has no meaning without concurrent transactions, a deferred constraint
trigger has none without a COMMIT, and `pg_notify` delivers nothing inside a
transaction that always rolls back. A mocked database would test none of the
things most likely to be wrong.

## Backend

### Async

`async def` everywhere, native uvicorn, asyncpg. Correct **because** nothing
here runs under a WSGI shim or gevent workers. If you deploy behind one, async
handlers block the event loop on every database call and this rule inverts.

### Logging

structlog. The first argument is a **static string** — no f-strings. It is the
event name you grep and aggregate on; interpolating makes every occurrence
unique and destroys that. Everything else is a keyword argument, every value a
primitive.

```python
# Good
logger.info("transfer initiated", transfer_id=str(t.id), amount_minor=t.amount_minor)

# Bad: interpolated message, ORM object in kwargs
logger.info(f"initiated {t.id}", transfer=t)
```

Never log a recipient's name or number. Ids, amounts, counts and durations are
fine; **an msisdn is personal data and does not belong in a log aggregator.**

**Every log line carries a request id**, and none of them pass it explicitly.
`RequestContextMiddleware` binds it into `structlog.contextvars`. The id is
stored on the `tasks` row when work is enqueued and re-bound by the worker when
it claims it, so one `grep` returns both halves of a disbursement across two
processes that share no memory. Without it, "why did this payment take four
minutes" is two investigations.

That middleware is pure ASGI rather than `BaseHTTPMiddleware` on purpose: the
convenient base class buffers the response body.

### Configuration

Everything environment-dependent is a field on `Settings` in `config.py`.
`os.getenv` anywhere else is a bug — a typo in an env var name should fail at
startup with a clear error, not at 2am with a `None`. A structural test enforces
it.

### Money

**Integers, in minor units, everywhere.** Never a float, never `Numeric` in a
model: a float cannot represent 0.10, and a ledger that cannot add up its own
rows exactly is not a ledger. `amount_minor` is always positive and `direction`
carries the sign, so a query can sum debits and credits separately without a
CASE.

The browser converts only at the edges (`money.ts`) and the value on the wire is
always the integer. It displays in one pinned locale, US English, rather than
the reader's: the same balance reading `1.000.000,00` on one operator's screen
and `1,000,000.00` on another's is how a figure gets read back wrong.

### Responses and errors

Every response is `{"data": ..., "meta": {...}}`. The wire is **camelCase**;
Python is **snake_case**; `wire.ApiSchema` converts at the boundary.

Catch a chain, most specific first. A single broad `except` collapses "wait and
retry" into "this will never work", and the caller can no longer tell them
apart.

### Authentication and authorization

`get_current_user()` in `api/deps.py` is the seam. With `OIDC_ISSUER` unset it
returns a constant and the app runs with no accounts at all — which keeps
`docker compose up` one command. Set the issuer and the same function verifies a
bearer token against that provider's JWKS.

**Do not pick a provider here.** Every serious one speaks OIDC. Two rules that
are not style preferences:

- **Name the algorithms.** `algorithms=["RS256", "ES256"]`, never "whatever the
  token says". A decoder that trusts the token's own `alg` accepts one signed
  with the public key as an HMAC secret.
- **Never tell the client why.** Expired, wrong audience and bad signature are
  one 401 with one message. The distinction belongs in your logs, where it helps
  you, not in a response body, where it helps whoever is probing.

Scope by `user_id` **in the WHERE clause**, never as an assertion afterwards. A
row belonging to someone else must be indistinguishable from one that does not
exist.

### Enforcement

`ruff check`, `ruff format --check` and `basedpyright` strict on `app/`, all in
CI. Ruff selects nearly every rule group and turns individual rules off *with a
note*, rather than selecting a short list: an `ignore` with a reason is a
decision someone can argue with later, and a short `select` is a decision nobody
wrote down.

## Frontend

- **Server state is TanStack Query. Local UI state is `useState`.**
- **Every view refreshes in the same moment.** Changes arrive over the event
  stream and `events.ts` re-reads the overview with every one; when the stream
  is down, `usePollInterval` gives every view the same cadence. Refreshing
  independently would let the console show a settled transfer beside a float
  balance from before it settled — which, for a ledger, reads as the books not
  adding up. This was a real bug, found by driving the UI rather than by a test.
  A new view whose numbers the worker can move needs both: a topic in
  `keysToInvalidate`, and `usePollInterval`.
- **Colours are semantic tokens** from the `@theme` block in `index.css`, never
  literal Tailwind palette classes. For a fraction of a colour use `ink`
  (`border-ink/10`), which inverts with the theme where `black` and `white` do
  not.
- **Loading, failed and empty are three different states**, and every panel
  shows which. An empty message must mean empty: "Nothing to report" under
  Reconciliation, or no dead letters, is reassurance, and it used to appear
  while the request was still in flight — or had failed. Loading is a
  skeleton the shape of what is coming (`Skeleton`, inside `Loading`, which
  sets `aria-busy` and says what is loading in words); a failed read is
  `LoadFailed`, which says so and that it is retrying.
- **`processing` is coloured as pending, not as success.** The money has been
  promised and has not arrived; sharing a colour with `succeeded` would tell an
  operator the payment landed when it has not.
- The view model and `api/` contain **no React**. The moment one imports a hook,
  testing it needs a renderer, which is how that suite stops existing.

## Checks

```bash
scripts/check.sh           lint, format, types and tests, both halves
scripts/check.sh --fast    the subset the pre-commit hook runs
```

**One script, three callers** — you, the hook, CI. A hook that checks something
different from CI is worse than no hook: it teaches you to trust a green that
does not mean anything. `--fast` drops only what is not a pure function of the
source: the integration suite, which needs Postgres, and `vite build`.

### Structural tests

Rules where the cost of the drift is high and the cost of the check is a regex
or an AST walk.

| Rule | Where |
| --- | --- |
| Only `config.py` reads the environment | backend |
| Routes do not build queries | backend |
| Every service function takes an explicit `session` | backend |
| `services/` imports neither `api/` nor `worker` | backend |
| Only `main.py` imports `app.api` at all | backend |
| Nothing outside `app/provider/` touches the provider's table | backend |
| Nothing outside `app/provider/` imports the mock | backend |
| Task kinds dispatch through the registry, not a branch in `execute` | backend |
| Registering one task kind twice is refused | backend |
| Every `__init__.py` is empty | backend |
| The browser knows every topic the server announces | backend |
| No literal Tailwind colour anywhere in `src/` | frontend |
| The view model and `api/` import no React | frontend |
| A component or hook file is named after what it exports | frontend |
| Every top-level `.ts` module has a test beside it | frontend |

**Prove a new one fails.** Break the convention on purpose, watch it go red, put
it back. A structural test that cannot fail is worse than none: it is a line in
this table that is not true. Two of the rules above were wrong on their first
run — the provider-table rule matched `ProviderPaymentView` as a substring and
flagged every correct caller — and only writing them and watching found that.

**Write the failure message as an argument, not an assertion.** The person who
hits one at 11pm is deciding whether the rule or their code is wrong.

## What is deliberately missing

Each is cheap to add later and expensive to build before you need it. The seam
each one needs already exists.

| Missing | Add it when | Seam that's ready |
| --- | --- | --- |
| Pagination | The transfer list gets long enough to notice | The `{data, meta}` envelope has the room |
| Rate limiting | More than one operator | — |
| Recipient enrolment UI | You tire of seeding | `recipient_service`, and the table takes the write |
| Multi-currency | A second programme | Accounts and journals are already per-currency; what is missing is an FX leg, not a column |
| Webhooks from the provider | Polling latency actually bothers somebody | `settle_transfer` already does the work; a webhook would just call it sooner |
| Approval workflow | Disbursements need a second pair of eyes | `pending` already means "authorised, not sent" |
| Recording a decision on a reported finding | Reported findings happen outside chaos testing | Findings are append-only facts; a decision is a second fact about one, not an edit — and for most kinds the real resolution is a correcting journal, which deserves its own design |

**Delete a row the day it stops being true.** A stale "deliberately missing"
entry is worse than no table: it is the document telling you not to look.

### Not here, on purpose

The chaos pass (deliberate provider breakage), the 10-million-row scale test,
`EXPLAIN ANALYZE` tuning with before/after numbers, and the live-traffic
migration exercise. The hooks for the first exist in `config.py`; the rest is
work whose value is in doing it.
