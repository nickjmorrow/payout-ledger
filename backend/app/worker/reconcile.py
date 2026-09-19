"""The reconciliation pass, as a task that keeps rescheduling itself.

Self-rescheduling rather than a cron or a timer thread, so the cadence lives in
the same queue as everything else: it survives a restart, it is visible in the
`tasks` table, and a worker that is down does not silently skip runs — the row
is simply claimed late.

**Duplicate reconcile tasks are harmless**, which is what makes the seeding
below safe without a lock. Two passes over the same window reach the same
conclusions; the second either finds nothing left to heal or writes a second
finding recording that the problem was still there. Neither loses money, and
paying for a lock to prevent a duplicate read is a worse trade.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.logging import get_logger
from app.models import Task
from app.provider.registry import get_provider
from app.services import reconciliation_service, task_service
from app.worker.handlers import TaskOutcome, register

logger = get_logger(__name__)

RECONCILE = "reconcile"


@register(RECONCILE)
async def reconcile(session: AsyncSession, task: Task) -> TaskOutcome:
    report = await reconciliation_service.run(session, provider=get_provider())

    # Rescheduled before committing, so the next run is queued in the same
    # transaction that recorded this one's findings. A crash between the two
    # would otherwise stop reconciliation forever — and the failure mode of
    # "the job that finds problems is the problem" is a bad one.
    await task_service.enqueue(
        session,
        kind=RECONCILE,
        run_at=task_service.seconds_from_now(settings.reconcile_interval_seconds),
        request_id=task.request_id,
    )
    await session.commit()

    if report.unresolved:
        logger.warning("reconciliation left unresolved findings", count=report.unresolved)
    return TaskOutcome("succeeded")


async def ensure_scheduled(session: AsyncSession) -> None:
    """Make sure a reconcile pass is queued. Called by the worker at startup.

    Without a seed, a queue that has never had one never gets one. With one,
    the handler above keeps the chain going by itself.
    """
    if await task_service.pending_count(session, kind=RECONCILE) > 0:
        return

    await task_service.enqueue(
        session,
        kind=RECONCILE,
        run_at=task_service.seconds_from_now(settings.reconcile_interval_seconds),
    )
    await session.commit()
    logger.info("reconciliation seeded")
