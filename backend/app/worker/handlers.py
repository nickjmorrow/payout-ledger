"""The task-kind registry, and `execute`, which runs a claimed task and settles its row.

A task kind is a registration in `HANDLERS`, never a branch in `execute`: the
settle path is the part that must not be duplicated per kind.
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

Status = Literal["succeeded", "failed"]


@dataclass(frozen=True)
class TaskOutcome:
    """How a handler finished.

    `retryable` is the handler's judgment; whether budget remains is the queue's.
    """

    status: Status
    error: str | None = None
    retryable: bool = False


Handler = Callable[[AsyncSession, Task], Awaitable[TaskOutcome]]

# The only list of task kinds. An unknown kind fails its own task, not the worker.
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
    # Re-bind the enqueuing request's id, so one grep finds both halves of a job.
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(task_id=str(task.id))
    if task.request_id:
        structlog.contextvars.bind_contextvars(request_id=task.request_id)

    # Captured before the run: a failed flush may expire `task`.
    task_id: uuid.UUID = task.id

    async with SessionFactory() as session:
        task = await session.merge(task)
        try:
            outcome = await _run(session, task)
        except Exception as exc:
            # A handler that raises fails its own row, retryably; the worker carries on.
            # Roll back before touching the session again: a failed flush leaves it
            # refusing every statement.
            await session.rollback()
            logger.exception("task raised", task_id=str(task_id))
            outcome = TaskOutcome("failed", error=f"{type(exc).__name__}: {exc}", retryable=True)

        # The row may have been deleted mid-run. `task_exists`, not `get_task`: the
        # identity map would report a deleted row as present.
        if not await task_service.task_exists(session, task_id=task_id):
            logger.info("task vanished mid-run", task_id=str(task_id))
            structlog.contextvars.clear_contextvars()
            return

        # A rollback expires `task`, and an implicit reload from async code raises
        # MissingGreenlet. Reload it explicitly.
        await session.refresh(task)

        # Retry if the handler says so and attempts remain; `attempts` counts this one.
        if outcome.status == "failed" and outcome.retryable and task.attempts < task.max_attempts:
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
