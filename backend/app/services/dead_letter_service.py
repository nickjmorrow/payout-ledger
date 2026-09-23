"""Deciding whether a dead-lettered task may run again.

The mechanics of retrying are the queue's (`task_service.requeue`). This is the
judgement, and it lives here because it needs to know what a task is *about* —
which the queue deliberately does not.

**A task about a finished transfer is not retried.** A disbursement that
exhausted its retries has, on the normal path, already reversed its transfer
and returned the money to the fund (`worker/disburse._handle_provider_error`).
Running its task again would find the transfer no longer pending and do
nothing — or, worse, look to an operator as though the payment had been sent
again. The honest instruction is to authorise a new disbursement, and the
refusal says so.

**A task about an unfinished transfer is retried, and that is the case this
exists for.** A worker that dies mid-send on the last attempt is dead-lettered
by the sweeper, which knows nothing about transfers — so the transfer stays
`pending` with the fund debited and nothing will ever move it. Retrying is
safe because both halves check before acting: the handler skips a transfer
that is no longer pending, and the provider dedupes on the transfer id.

Tasks about no transfer at all — a reconciliation pass — are always
retryable. Running one twice is harmless by design (`worker/reconcile.py`).
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
    """Requeue one dead-lettered task, if it is still worth running. **Does not commit.**

    The row is locked first, so two operators pressing Retry at once are
    serialised: the second sees a task that is no longer dead and is refused,
    rather than both requeueing it. That check is what makes repeating the
    request safe without an idempotency key — a repeat is refused, not doubled.
    """
    task = await session.get(Task, task_id, with_for_update=True)
    if task is None or task.status != "failed":
        raise NotDeadLetteredError(f"task {task_id} is not in the dead-letter queue")

    raw = task.payload.get("transfer_id")
    if isinstance(raw, str):
        transfer = await transfer_service.get(session, transfer_id=uuid.UUID(raw))
        if transfer is not None and transfer.status in FINISHED:
            what = "was paid" if transfer.status == "succeeded" else "was reversed"
            raise AlreadyFinishedError(
                f"the transfer this task was about {what}, so running it again would do "
                "nothing. To pay this recipient, authorise a new disbursement."
            )

    await task_service.requeue(session, task=task)
    logger.info("dead letter retried", task_id=str(task_id), kind=task.kind)
    return task
