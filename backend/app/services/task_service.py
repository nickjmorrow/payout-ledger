"""The queue.

Postgres is the whole thing. The parts that matter are all in the SQL below:

* **Claiming** is `select … for update skip locked`. Two workers running the
  same query take different rows instead of one waiting on the other, and a
  crashed worker's row stays locked only as long as its transaction lives.
* **Waking** is NOTIFY on one global channel. Without it a worker polls and
  every enqueued task waits half a poll interval for no reason.
* **Sweeping** is what makes the claim safe. A worker that is killed mid-task
  leaves a row marked running forever; nothing in Postgres notices, because
  nothing in Postgres knows the worker existed. The sweeper is that knowledge.

Read those three together and you have the design. Everything else here is
bookkeeping.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.bus import announce, publish
from app.config import settings
from app.logging import get_logger
from app.models import Task

logger = get_logger(__name__)

# One channel for "there is work", rather than one per kind. The worker's next
# move after waking is to ask the database anyway.
WAKE_CHANNEL = "tasks_new"

_CLAIM = text(
    """
    with claimed as (
        select id from tasks
         where status = 'pending' and run_at <= now()
         order by run_at
           for update skip locked
         limit 1
    )
    update tasks t
       set status = 'running',
           attempts = t.attempts + 1,
           claimed_at = now(),
           claimed_by = :worker_id,
           updated_at = now()
      from claimed
     where t.id = claimed.id
    returning t.id, t.payload ->> 'transfer_id' as transfer_id
    """
)

_SWEEP = text(
    """
    update tasks
       set status = case when attempts >= max_attempts then 'failed' else 'pending' end,
           error = case when attempts >= max_attempts then 'worker died mid-task' else error end,
           claimed_at = null,
           claimed_by = null,
           updated_at = now()
     where status = 'running'
       and claimed_at < now() - make_interval(secs => :stale_seconds)
    returning id, payload ->> 'transfer_id' as transfer_id
    """
)

_RETRY = text(
    """
    update tasks
       set status = 'pending',
           claimed_at = null,
           claimed_by = null,
           error = :error,
           run_at = now() + make_interval(secs => :delay),
           updated_at = now()
     where id = :task_id
    """
)


async def _announce(session: AsyncSession, *, task_id: uuid.UUID, transfer_id: object) -> None:
    """Tell open consoles a task moved, in the transaction that moved it.

    Every write to a task row below goes through here, so the operations view
    never has to poll to see a claim or a retry. `transfer_id` arrives straight
    off a JSONB payload and is only trusted if it is a string.
    """
    await announce(
        session,
        topic="tasks",
        subject_id=task_id,
        transfer_id=transfer_id if isinstance(transfer_id, str) else None,
    )


async def enqueue(
    session: AsyncSession,
    *,
    kind: str,
    payload: dict[str, Any] | None = None,
    run_at: datetime | None = None,
    request_id: str | None = None,
) -> Task:
    """Put work on the queue. **Does not commit — this is the outbox.**

    Enqueueing is almost never the only thing a request does. A disbursement
    writes a transfer, posts a journal, records an idempotency key, *and* asks
    for the payment to be sent; all four have to land together. If this
    committed, the task would be durable before the rows describing it were,
    and a crash in that window leaves a worker holding a job whose subject does
    not exist.

    Writing the job into a table inside the caller's transaction is the outbox
    pattern, and here it needs no extra machinery because the queue already
    *is* a table. The NOTIFY below is transactional too, so the worker is woken
    at the same commit and never before it. See AGENTS.md > The outbox.
    """
    task = Task(
        kind=kind,
        payload=payload or {},
        request_id=request_id,
        **({"run_at": run_at} if run_at is not None else {}),
    )
    session.add(task)
    # Flushed, not committed: the row needs an id for the log line and for the
    # caller to reference, but the transaction stays open.
    await session.flush()

    # Only worth waking a worker for something it can claim right now.
    if run_at is None:
        await publish(session, WAKE_CHANNEL, {"task_id": str(task.id)})
    await _announce(session, task_id=task.id, transfer_id=task.payload.get("transfer_id"))

    logger.info("task enqueued", task_id=str(task.id), kind=kind)
    return task


async def claim_next(session: AsyncSession, *, worker_id: str) -> Task | None:
    """Take the oldest due task, or None. Safe to call from any number of workers."""
    row = (await session.execute(_CLAIM, {"worker_id": worker_id})).first()
    if row is None:
        await session.commit()
        return None

    task_id: uuid.UUID = row.id
    await _announce(session, task_id=task_id, transfer_id=row.transfer_id)
    await session.commit()

    # populate_existing, because the claim was a raw UPDATE and this session may
    # already hold a copy of that row from enqueueing it. With
    # expire_on_commit=False a plain get() would hand back the stale copy — and
    # `attempts` is exactly what the retry decision reads, so a stale one
    # silently gets the retry budget wrong.
    task = await session.get(Task, task_id, populate_existing=True)
    logger.info("task claimed", task_id=str(task_id), worker_id=worker_id)
    return task


async def finish(
    session: AsyncSession, *, task: Task, status: str, error: str | None = None
) -> None:
    """Settle a task permanently."""
    task.status = status
    task.error = error
    # func.now() rather than a Python timestamp: every other time in this table
    # comes from Postgres, and two clocks in one table means any ordering
    # question is decided by container clock skew.
    task.updated_at = func.now()
    await _announce(session, task_id=task.id, transfer_id=task.payload.get("transfer_id"))
    await session.commit()
    logger.info("task finished", task_id=str(task.id), status=status)


async def retry_later(
    session: AsyncSession, *, task: Task, error: str, delay_seconds: float
) -> None:
    """Requeue a failed task to run again after a delay."""
    await session.execute(_RETRY, {"task_id": task.id, "error": error, "delay": delay_seconds})
    await _announce(session, task_id=task.id, transfer_id=task.payload.get("transfer_id"))
    await session.commit()
    logger.warning(
        "task retrying",
        task_id=str(task.id),
        attempt=task.attempts,
        max_attempts=task.max_attempts,
        delay_seconds=round(delay_seconds, 1),
    )


def seconds_from_now(seconds: float) -> datetime:
    """A `run_at` this far ahead.

    Here rather than at each call site so that every delayed enqueue uses the
    same clock — and it is Python's rather than Postgres's only because
    `run_at` is compared against `now()` on the server, which makes any skew
    between the two a scheduling wobble rather than a correctness problem.
    """
    return datetime.now(UTC) + timedelta(seconds=seconds)


def retry_delay_seconds(attempts: int) -> float:
    """Exponential backoff, capped.

    Doubling matters more than the exact numbers: the failures worth retrying
    are rate limits and provider outages, and hammering either one is how a
    transient problem becomes a sustained one.
    """
    return min(
        settings.task_retry_base_seconds * (2 ** max(0, attempts - 1)),
        settings.task_retry_max_seconds,
    )


async def get_task(session: AsyncSession, *, task_id: uuid.UUID) -> Task | None:
    return await session.get(Task, task_id)


async def task_exists(session: AsyncSession, *, task_id: uuid.UUID) -> bool:
    """Is this row still in the table?

    Not `get_task(...) is not None`, and the difference is the whole reason
    this exists. `session.get` answers from the identity map when the session
    has already loaded that row — which it always has, by the time a worker is
    settling a task it just ran — so it returns the in-memory instance without
    asking Postgres anything, and reports a row that was deleted underneath it
    as present. This selects a scalar instead, so there is nothing to serve
    from the identity map and the answer comes from the database.
    """
    result = await session.execute(select(exists().where(Task.id == task_id)))
    return bool(result.scalar())


async def sweep_stale(session: AsyncSession, *, stale_seconds: int | None = None) -> int:
    """Return tasks abandoned by a dead worker to the queue.

    `stale_seconds` is an argument rather than a straight settings read so a
    test can make a task stale without reaching into a global.
    """
    if stale_seconds is None:
        stale_seconds = settings.task_stale_seconds
    result = await session.execute(_SWEEP, {"stale_seconds": stale_seconds})
    rows = result.all()
    for row in rows:
        await _announce(session, task_id=row.id, transfer_id=row.transfer_id)
    ids = [row.id for row in rows]
    await session.commit()
    if ids:
        logger.warning("tasks swept", count=len(ids))
    return len(ids)


async def dead_lettered(session: AsyncSession, *, limit: int = 50) -> list[Task]:
    """Tasks that exhausted their retries and are parked for a human.

    **This is the dead-letter queue.** It is not a second table, because it
    does not need to be: a task that has run out of attempts is a row with
    `status = 'failed'` and the error that stopped it, and moving it somewhere
    else would only lose the attempt history and the payload alongside it.

    What makes it a DLQ rather than a leak is that something is expected to
    look. A failed disbursement has already reversed its transfer — see
    `worker/disburse._handle_provider_error` — so the money is not stuck; what
    is left here is the evidence of why.
    """
    result = await session.execute(
        select(Task).where(Task.status == "failed").order_by(Task.updated_at.desc()).limit(limit)
    )
    return list(result.scalars())


async def for_transfer(session: AsyncSession, *, transfer_id: uuid.UUID) -> list[Task]:
    """Every task that has ever been about one transfer, oldest first.

    The send, the settlement checks, and every retry of either: what a person
    reads to answer "why did this payment take four minutes". Matched on the
    payload rather than a column, because a task is generic work and the
    transfer is one of the things it can be about; `tasks_transfer_idx` keeps
    the lookup off a sequential scan.
    """
    result = await session.execute(
        select(Task)
        .where(Task.payload["transfer_id"].astext == str(transfer_id))
        .order_by(Task.created_at)
    )
    return list(result.scalars())


@dataclass(frozen=True)
class QueueCounts:
    """The queue at a glance.

    `due` and `scheduled` are both `pending`, split on `run_at`: work a worker
    would claim right now, and work that is deliberately waiting — a settlement
    check a few seconds out, a retry backing off, the next reconciliation pass.
    Lumping them together would make a healthy queue with a dozen scheduled
    checks look like a backlog.
    """

    due: int
    scheduled: int
    running: int
    dead: int


async def counts(session: AsyncSession) -> QueueCounts:
    """One pass over the table, four FILTERed counts."""
    pending = Task.status == "pending"
    row = (
        await session.execute(
            select(
                func.count().filter(and_(pending, Task.run_at <= func.now())),
                func.count().filter(and_(pending, Task.run_at > func.now())),
                func.count().filter(Task.status == "running"),
                func.count().filter(Task.status == "failed"),
            )
        )
    ).one()
    return QueueCounts(due=row[0], scheduled=row[1], running=row[2], dead=row[3])


async def active(session: AsyncSession, *, limit: int = 50) -> list[Task]:
    """Everything not yet finished — running, due, and scheduled — soonest first."""
    result = await session.execute(
        select(Task)
        .where(Task.status.in_(("pending", "running")))
        .order_by(Task.run_at)
        .limit(limit)
    )
    return list(result.scalars())


async def requeue(session: AsyncSession, *, task: Task, extra_attempts: int = 3) -> None:
    """Put a dead-lettered task back on the queue with a fresh budget. **Does not commit.**

    The budget is extended rather than reset: `max_attempts` rises by
    `extra_attempts` and `attempts` is left alone, so a task that failed three
    times and was retried reads 3 of 6, not 0 of 3. The history of how it got
    to the dead-letter queue is the most useful thing about it, and zeroing the
    count would erase it. `error` is kept for the same reason, until a new
    outcome replaces it.

    Whether a task *should* be retried is not decided here. See
    `dead_letter_service.retry`, which knows what a task is about.
    """
    task.status = "pending"
    task.max_attempts = task.attempts + extra_attempts
    task.claimed_at = None
    task.claimed_by = None
    task.run_at = func.now()
    task.updated_at = func.now()
    await session.flush()
    # The two `now()`s above are expired by the flush, and reading an expired
    # attribute from async code is implicit IO. Reload them explicitly.
    await session.refresh(task)
    await publish(session, WAKE_CHANNEL, {"task_id": str(task.id)})
    await _announce(session, task_id=task.id, transfer_id=task.payload.get("transfer_id"))
    logger.info(
        "task requeued",
        task_id=str(task.id),
        attempts=task.attempts,
        max_attempts=task.max_attempts,
    )


async def schedule_once(
    session: AsyncSession,
    *,
    kind: str,
    run_at: datetime,
    request_id: str | None = None,
    successor_of: uuid.UUID | None = None,
) -> Task | None:
    """Enqueue `kind` unless one is already scheduled or running. **Does not commit.**

    For work that should exist exactly once — a recurring job's next run. Two
    things make "exactly once" true, and a pending count alone had neither:

    **A running task counts.** It is about to schedule its own successor, so a
    second chain seeded while it runs is a second chain forever. With one
    worker that window never mattered; with two, one worker's loop fell into
    it while the other ran the pass.

    **The check and the insert are serialised** by a transaction-scoped
    advisory lock keyed on the kind, released at the caller's COMMIT or
    ROLLBACK. Without it, two workers starting together both count zero and
    both insert. The lock is the only coordination, not a column or a table,
    because there is nothing to store: the rows already are the answer.

    `successor_of` is the task asking on its own behalf. It is running, and must
    not count as the thing it is scheduling. And because it asks under the
    same lock, a chain that finds another already pending does not continue —
    so duplicate chains, from before this existed, fold into one.
    """
    await session.execute(
        text("select pg_advisory_xact_lock(hashtext(:key))"), {"key": f"schedule_once:{kind}"}
    )
    query = select(func.count()).where(Task.kind == kind, Task.status.in_(("pending", "running")))
    if successor_of is not None:
        query = query.where(Task.id != successor_of)
    if (await session.execute(query)).scalar_one() > 0:
        return None
    return await enqueue(session, kind=kind, run_at=run_at, request_id=request_id)


async def pending_count(session: AsyncSession, *, kind: str) -> int:
    result = await session.execute(
        select(func.count()).select_from(Task).where(Task.kind == kind, Task.status == "pending")
    )
    return int(result.scalar_one())
