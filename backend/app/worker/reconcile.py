"""The reconciliation pass, as a task that keeps rescheduling itself.

Self-rescheduling rather than a cron or a timer thread, so the cadence lives in
the same queue as everything else: it survives a restart, it is visible in the
`tasks` table, and a worker that is down does not silently skip runs — the row
is simply claimed late.

**One chain, however many workers.** Every scheduling of a pass — the seed
below and each pass's own successor — goes through
`task_service.schedule_once`, which counts running passes as well as pending
ones and serialises the check with an advisory lock. This used to be a
pending count and an argument that duplicates were harmless. Each duplicate
pass was; the duplicate *chain* was not. With two workers, one worker's loop
would see no pending pass while the other was running one, seed a second
chain, and the two would then run side by side forever — with every repeat
of the race adding another. It was spotted on the console's queue panel,
two passes both due in 29 seconds.

A single duplicate pass is still harmless, and that is still why nothing here
needs to be more careful than this: two passes over one window reach the same
conclusions, and neither moves money the other did not.
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
    #
    # `successor_of`, so this pass does not count as the pass it is
    # scheduling; and if another chain already has one pending, this one ends.
    await task_service.schedule_once(
        session,
        kind=RECONCILE,
        run_at=task_service.seconds_from_now(settings.reconcile_interval_seconds),
        request_id=task.request_id,
        successor_of=task.id,
    )
    await session.commit()

    if report.unresolved:
        logger.warning("reconciliation left unresolved findings", count=report.unresolved)
    return TaskOutcome("succeeded")


async def ensure_scheduled(session: AsyncSession) -> None:
    """Make sure a reconcile pass is queued or running. Called on every worker pass.

    Without a seed, a queue that has never had one never gets one. With one,
    the handler above keeps the chain going by itself.
    """
    seeded = await task_service.schedule_once(
        session,
        kind=RECONCILE,
        run_at=task_service.seconds_from_now(settings.reconcile_interval_seconds),
    )
    # Commits either way: the advisory lock is held until the transaction ends,
    # and a worker idling in an open transaction would hold it indefinitely.
    await session.commit()
    if seeded is not None:
        logger.info("reconciliation seeded")
