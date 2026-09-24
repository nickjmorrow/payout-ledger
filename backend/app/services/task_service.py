"""The queue, in Postgres: claim with SKIP LOCKED, wake with NOTIFY, sweep the dead.

See AGENTS.md > Postgres is the queue.
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

# One channel for "there is work": after waking, a worker asks the table anyway.
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
    """Tell open consoles a task moved. `transfer_id` comes off JSONB, so check its type."""
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
    """Put work on the queue. Does not commit: see AGENTS.md > The outbox."""
    task = Task(
        kind=kind,
        payload=payload or {},
        request_id=request_id,
        **({"run_at": run_at} if run_at is not None else {}),
    )
    session.add(task)
    await session.flush()

    # Only wake a worker for something it can claim now.
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

    # populate_existing: the claim was a raw UPDATE, and this session may hold a
    # stale copy of the row whose `attempts` the retry decision reads.
    task = await session.get(Task, task_id, populate_existing=True)
    logger.info("task claimed", task_id=str(task_id), worker_id=worker_id)
    return task


async def finish(
    session: AsyncSession, *, task: Task, status: str, error: str | None = None
) -> None:
    """Settle a task permanently."""
    task.status = status
    task.error = error
    # Postgres's clock, like every other time in this table.
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
    """A `run_at` this far ahead. Clock skew with Postgres only nudges when it runs."""
    return datetime.now(UTC) + timedelta(seconds=seconds)


def retry_delay_seconds(attempts: int) -> float:
    """Exponential backoff, capped: hammering an outage prolongs it."""
    return min(
        settings.task_retry_base_seconds * (2 ** max(0, attempts - 1)),
        settings.task_retry_max_seconds,
    )


async def get_task(session: AsyncSession, *, task_id: uuid.UUID) -> Task | None:
    return await session.get(Task, task_id)


async def task_exists(session: AsyncSession, *, task_id: uuid.UUID) -> bool:
    """Is this row still in the table, according to Postgres?

    Not `get_task(...) is not None`: `session.get` answers from the identity map
    and would report a row deleted underneath the worker as present.
    """
    result = await session.execute(select(exists().where(Task.id == task_id)))
    return bool(result.scalar())


async def sweep_stale(session: AsyncSession, *, stale_seconds: int | None = None) -> int:
    """Return tasks abandoned by a dead worker to the queue, or fail them if out of attempts."""
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
    """The dead-letter queue: failed tasks, newest first. See AGENTS.md > Retries."""
    result = await session.execute(
        select(Task).where(Task.status == "failed").order_by(Task.updated_at.desc()).limit(limit)
    )
    return list(result.scalars())


async def for_transfer(session: AsyncSession, *, transfer_id: uuid.UUID) -> list[Task]:
    """Every task that has been about one transfer, oldest first. Uses `tasks_transfer_idx`."""
    result = await session.execute(
        select(Task)
        .where(Task.payload["transfer_id"].astext == str(transfer_id))
        .order_by(Task.created_at)
    )
    return list(result.scalars())


async def dead_letter_for(
    session: AsyncSession, *, kind: str, transfer_id: uuid.UUID
) -> Task | None:
    """The dead-lettered `kind` task about this transfer, locked, or None.

    Locked so a dead-letter retry waits for whatever the caller decides.
    """
    result = await session.execute(
        select(Task)
        .where(
            Task.kind == kind,
            Task.status == "failed",
            Task.payload["transfer_id"].astext == str(transfer_id),
        )
        .with_for_update()
    )
    return result.scalars().first()


@dataclass(frozen=True)
class QueueCounts:
    """The queue at a glance.

    `due` and `scheduled` are both pending, split on `run_at`, so work that is
    deliberately waiting does not look like a backlog.
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
    """Put a dead-lettered task back on the queue. Does not commit.

    Extends the budget rather than resetting it, so the attempt history stays
    readable. Whether to retry is `dead_letter_service`'s decision.
    """
    task.status = "pending"
    task.max_attempts = task.attempts + extra_attempts
    task.claimed_at = None
    task.claimed_by = None
    task.run_at = func.now()
    task.updated_at = func.now()
    await session.flush()
    # The flush expires the two `now()` attributes; reload them explicitly
    # rather than lazily, which async code cannot do.
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
    """Enqueue `kind` unless one is already pending or running. Does not commit.

    For recurring work that must exist exactly once. A running task counts,
    because it will schedule its own successor, and a transaction-scoped
    advisory lock serializes the check with the insert. `successor_of` is a
    task scheduling its own next run: it is excluded from the count, and ends
    its chain if another is already pending. See AGENTS.md > Recurring work
    runs once.
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
