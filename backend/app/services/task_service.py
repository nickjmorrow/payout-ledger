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
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.bus import publish
from app.config import settings
from app.logging import get_logger
from app.models import Task

logger = get_logger(__name__)

# One channel for "there is work", rather than one per kind. The worker's next
# move after waking is to ask the database anyway.
WAKE_CHANNEL = "tasks_new"

# A task is terminal when nothing will move it again. The streaming endpoint
# uses this to decide when to hang up.
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

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
    returning t.id
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
    returning id
    """
)

_RELEASE = text(
    """
    update tasks
       set status = 'pending', claimed_at = null, claimed_by = null, updated_at = now()
     where id = :task_id
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

    logger.info("task enqueued", task_id=str(task.id), kind=kind)
    return task


async def claim_next(session: AsyncSession, *, worker_id: str) -> Task | None:
    """Take the oldest due task, or None. Safe to call from any number of workers."""
    result = await session.execute(_CLAIM, {"worker_id": worker_id})
    task_id = result.scalar()
    await session.commit()

    if task_id is None:
        return None

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
    await session.commit()
    logger.info("task finished", task_id=str(task.id), status=status)


async def release(session: AsyncSession, *, task: Task) -> None:
    """Put a claimed task back on the queue, unchanged.

    For a worker shutting down cleanly: it did not fail, it just will not be
    the one to finish it. Another worker picks it up on its next claim, which
    is seconds — rather than the `task_stale_seconds` the sweeper would take to
    reach the same conclusion about a worker that vanished without saying so.

    `attempts` is deliberately NOT decremented. A task that keeps being handed
    around deserves to hit `max_attempts` eventually; that is the difference
    between an unlucky task and one that kills whatever picks it up.
    """
    await session.execute(_RELEASE, {"task_id": task.id})
    await session.commit()
    logger.info("task released", task_id=str(task.id), attempts=task.attempts)


async def retry_later(
    session: AsyncSession, *, task: Task, error: str, delay_seconds: float
) -> None:
    """Requeue a failed task to run again after a delay."""
    await session.execute(_RETRY, {"task_id": task.id, "error": error, "delay": delay_seconds})
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


async def cancel_requested(session: AsyncSession, *, task_id: uuid.UUID) -> bool:
    """Should this task stop early?

    Cooperative, because the worker is a different process and cannot be
    interrupted from outside. A long-running handler checks this between units
    of work, so a stop takes effect within one unit rather than instantly.

    A row that has vanished counts as cancelled. There is nothing left to
    settle and nobody to report to, so carrying on can only write orphans.
    """
    result = await session.execute(
        select(Task.cancel_requested, Task.status).where(Task.id == task_id)
    )
    row = result.first()
    if row is None:
        return True
    return bool(row.cancel_requested) or row.status in TERMINAL_STATUSES


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


async def request_cancel(session: AsyncSession, *, task: Task) -> None:
    task.cancel_requested = True
    # A task that has not started can be settled immediately; there is no worker
    # to cooperate with yet.
    if task.status == "pending":
        task.status = "cancelled"
    await session.commit()
    logger.info("task cancel requested", task_id=str(task.id), status=task.status)


async def sweep_stale(session: AsyncSession, *, stale_seconds: int | None = None) -> int:
    """Return tasks abandoned by a dead worker to the queue.

    `stale_seconds` is an argument rather than a straight settings read so a
    test can make a task stale without reaching into a global.
    """
    if stale_seconds is None:
        stale_seconds = settings.task_stale_seconds
    result = await session.execute(_SWEEP, {"stale_seconds": stale_seconds})
    ids = [row[0] for row in result.all()]
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


async def pending_count(session: AsyncSession, *, kind: str) -> int:
    result = await session.execute(
        select(func.count()).select_from(Task).where(Task.kind == kind, Task.status == "pending")
    )
    return int(result.scalar_one())
