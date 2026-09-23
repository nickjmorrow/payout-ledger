"""Deciding whether a dead-lettered task may run again.

Refused for a task about a transfer that already settled or was reversed:
rerunning it would do nothing. Allowed for an unfinished transfer, which is
safe because the handler and the provider both check before acting. See
AGENTS.md > Retries, and the dead-letter queue.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import Task
from app.services import task_service, transfer_service

logger = get_logger(__name__)

FINISHED = frozenset({"succeeded", "failed"})


class DeadLetterError(Exception):
    """Base for every refusal in this module."""


class NotDeadLetteredError(DeadLetterError):
    """No such task, or it is not in the dead-letter queue."""


class AlreadyFinishedError(DeadLetterError):
    """The transfer this task was about has settled or been reversed."""


async def retry(session: AsyncSession, *, task_id: uuid.UUID) -> Task:
    """Requeue one dead-lettered task, if it is still worth running. Does not commit.

    The row lock makes a repeated request refused rather than doubled, so no
    idempotency key is needed.
    """
    task = await session.get(Task, task_id, with_for_update=True)
    if task is None or task.status != "failed":
        raise NotDeadLetteredError(
            "This task is not in the dead-letter queue; it may already have been retried."
        )

    raw = task.payload.get("transfer_id")
    if isinstance(raw, str):
        transfer = await transfer_service.get(session, transfer_id=uuid.UUID(raw))
        if transfer is not None and transfer.status in FINISHED:
            what = "was paid" if transfer.status == "succeeded" else "was reversed"
            raise AlreadyFinishedError(
                f"The transfer this task was about {what}, so running it again would do "
                "nothing. To pay this recipient, authorize a new disbursement."
            )

    await task_service.requeue(session, task=task)
    logger.info("dead letter retried", task_id=str(task_id), kind=task.kind)
    return task
