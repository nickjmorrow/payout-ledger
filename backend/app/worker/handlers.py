"""What each kind of task means, and how its row is settled afterwards.

`execute` at the bottom is the one entry point: it runs a claimed task,
survives anything the handler throws at it, and leaves the row in a state the
queue agrees with.

**A task kind is a registration in `HANDLERS`, not a branch in `execute`.** The
settle path below is the part that is hard to get right and the part that must
not be duplicated per kind, so adding work means writing a function and adding
a line to the table — never editing `execute`.

Most of the length here is that settle path, and most of *that* is a comment
about the way a row deleted mid-task used to wedge the worker. Read it before
touching it.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionFactory
from app.logging import get_logger
from app.models import Task
from app.services import task_service

if TYPE_CHECKING:
    import uuid

logger = get_logger(__name__)

Status = Literal["succeeded", "failed", "interrupted"]


@dataclass(frozen=True)
class TaskOutcome:
    """How a handler finished, in the vocabulary the settle path understands.

    `retryable` is the handler's judgement and nothing else's. The queue owns
    *whether* there is budget left to retry — see `execute` — but only the
    handler knows whether trying again could possibly help. A provider timeout
    is retryable; a transfer whose amount does not parse never will be.
    """

    status: Status
    error: str | None = None
    retryable: bool = False


Handler = Callable[[AsyncSession, Task], Awaitable[TaskOutcome]]

# The registry, and the only place task kinds are listed. An unknown kind fails
# its own task rather than raising, so a bad enqueue costs one row and not the
# worker.
HANDLERS: dict[str, Handler] = {}


def register(kind: str) -> Callable[[Handler], Handler]:
    def decorator(handler: Handler) -> Handler:
        if kind in HANDLERS:
            raise ValueError(f"task kind {kind!r} is already registered")
        HANDLERS[kind] = handler
        return handler

    return decorator


async def _run(session: AsyncSession, task: Task) -> TaskOutcome:
    handler = HANDLERS.get(task.kind)
    if handler is None:
        return TaskOutcome("failed", error=f"unknown task kind {task.kind!r}")
    return await handler(session, task)


async def execute(task: Task) -> None:
    """Run one claimed task and settle its row, whatever happens."""
    # Re-bind the request id the API recorded when it enqueued this, so one
    # `grep` finds both halves of a job across two processes. Null for work the
    # worker created itself, which gets the task id alone.
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(task_id=str(task.id))
    if task.request_id:
        structlog.contextvars.bind_contextvars(request_id=task.request_id)

    # Captured before the run, so the error path never has to read an attribute
    # off an instance a failed flush may have expired.
    task_id: uuid.UUID = task.id

    async with SessionFactory() as session:
        task = await session.merge(task)
        try:
            outcome = await _run(session, task)
        except Exception as exc:
            # The worker outlives every task it runs. A handler that raises
            # marks its own row failed and the loop continues — the alternative
            # is one bad task taking the queue down with it. Retryable, because
            # an unexpected exception is more often a blip than a certainty.
            #
            # **Roll back first, before anything else touches this session.** A
            # failed flush — an IntegrityError above all — leaves the session
            # refusing every further statement until it is rolled back, and it
            # expires every instance it holds. `task_id` above is the local
            # captured for that reason: reading `task.id` here would lazy-load
            # an expired attribute, which is a statement, which raises
            # PendingRollbackError *from inside the logging call*. The exception
            # then escapes the `try` whose whole job is to contain it, and the
            # worker stops claiming anything, forever.
            await session.rollback()
            logger.exception("task raised", task_id=str(task_id))
            outcome = TaskOutcome("failed", error=f"{type(exc).__name__}: {exc}", retryable=True)

        # The row can be deleted while its task is running, and then there is
        # nothing to settle and every statement below is an UPDATE matching
        # zero rows.
        #
        # `task_exists` and not `get_task`, deliberately: this session loaded
        # that row before the run, so `session.get` answers from the identity
        # map without asking Postgres and cheerfully reports a deleted row as
        # present. The settle below then UPDATEs zero rows, SQLAlchemy raises
        # StaleDataError, and the worker is wedged for the second time by the
        # same delete.
        if not await task_service.task_exists(session, task_id=task_id):
            logger.info("task vanished mid-run", task_id=str(task_id))
            structlog.contextvars.clear_contextvars()
            return

        # Reloaded under an explicit await, because a rollback — the one above,
        # or one inside a handler — expires `task` along with everything else
        # in the session. Capturing `task_id` covers the log line, but the
        # settle path below reads `attempts`, `max_attempts` and `payload` too,
        # and each of those on an expired instance is an implicit load: from
        # async code that is MissingGreenlet, raised outside the `try`, and the
        # worker stops. That is what this did to every handler that raised,
        # until a test made one raise. The row is known to exist, just above.
        await session.refresh(task)

        # Shutting down: hand the work back rather than settle it. Nothing about
        # this task failed, and the next worker to claim it starts again.
        if outcome.status == "interrupted":
            await task_service.release(session, task=task)

        # Worth another go, and attempts left. `attempts` was incremented by the
        # claim, so it already counts this one.
        elif outcome.status == "failed" and outcome.retryable and task.attempts < task.max_attempts:
            await task_service.retry_later(
                session,
                task=task,
                error=outcome.error or "",
                delay_seconds=task_service.retry_delay_seconds(task.attempts),
            )

        else:
            await task_service.finish(
                session, task=task, status=outcome.status, error=outcome.error
            )

    structlog.contextvars.clear_contextvars()
